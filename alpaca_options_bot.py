import time
import numpy as np
import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.trading.requests import MarketOrderRequest, GetOptionContractsRequest
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta
from config import ALPACA_CONFIG

API_KEY = ALPACA_CONFIG["apiKey"]
API_SECRET = ALPACA_CONFIG["apiSecret"]

# Initialize clients
trading_client = TradingClient(API_KEY, API_SECRET, paper=True)
stock_data_client = StockHistoricalDataClient(API_KEY, API_SECRET)

class StraddlePosition:
    def __init__(self, call_contract, put_contract, entry_iv, entry_price, quantity):
        self.call_contract = call_contract
        self.put_contract = put_contract
        self.entry_iv = entry_iv
        self.entry_price = entry_price
        self.quantity = quantity
        self.entry_time = datetime.utcnow()

def get_historical_volatility(symbol, days=30):
    try:
        end = datetime.now() - timedelta(days=1)
        start = end - timedelta(days=int(days * 2))

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            adjustment='all'
        )

        bars = stock_data_client.get_stock_bars(request)
        df = bars.df

        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index(level='symbol', drop=True)
        df = df[~df.index.duplicated()].sort_index()

        df = df.asfreq('D', method='pad')
        closes = df['close'].ffill().dropna()

        if len(closes) < 20:
            return 0.0

        log_returns = np.log(closes / closes.shift(1)).dropna()
        return np.sqrt(252) * log_returns.std()

    except Exception as e:
        print(f"HV Error: {str(e)}")
        return 0.0

def get_current_iv(symbol):
    try:
        # Retrieve option contracts
        contracts = trading_client.get_option_contracts(
            GetOptionContractsRequest(underlying_symbol=symbol)
        )

        # Debugging: check data type
        if isinstance(contracts, list) and isinstance(contracts[0], tuple):
            contracts = [c[1] for c in contracts]  # Extract the actual dictionary

        # Extract valid expiration dates
        expirations = [c['expiration_date'] for c in contracts if isinstance(c, dict) and c.get('expiration_date')]
        if not expirations:
            print(f"No valid expiration dates found for {symbol}")
            return 0.0

        # Find the nearest expiration date
        nearest_exp = min(expirations)

        # Get ATM strike
        last_price = stock_data_client.get_latest_trade(symbol).price
        strikes = sorted([c['strike_price'] for c in contracts if c['expiration_date'] == nearest_exp])
        if not strikes:
            print(f"No valid strikes found for {symbol}")
            return 0.0

        atm_strike = min(strikes, key=lambda x: abs(x - last_price))

        # Find ATM call and put contracts
        call = next((c for c in contracts if c['strike_price'] == atm_strike and c['type'].value == "call"), None)
        put = next((p for p in contracts if p['strike_price'] == atm_strike and p['type'].value == "put"), None)

        if not call or not put:
            print("Could not find both call and put options at ATM strike.")
            return 0.0

        # Compute implied volatility average
        iv_call = float(call.get('implied_volatility', 0))
        iv_put = float(put.get('implied_volatility', 0))
        return (iv_call + iv_put) / 2

    except Exception as e:
        print(f"IV Error: {str(e)}")
        return 0.0

def execute_straddle(symbol, quantity):
    try:
        # Retrieve option contracts
        expirations = trading_client.get_option_contracts(
            GetOptionContractsRequest(underlying_symbol=symbol)
        )
        nearest_exp = min([datetime.fromisoformat(c.expiration_date) for c in expirations])

        last_price = stock_data_client.get_latest_trade(symbol).price
        strikes = sorted([c.strike_price for c in expirations if c.expiration_date == nearest_exp.strftime('%Y-%m-%d')])
        atm_strike = min(strikes, key=lambda x: abs(x - last_price))

        # Find ATM call and put
        call = next(c for c in expirations if c.strike_price == atm_strike and c.option_type == "call")
        put = next(p for p in expirations if p.strike_price == atm_strike and p.option_type == "put")

        # Place orders
        call_order = MarketOrderRequest(
            symbol=call.symbol,
            qty=quantity,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            asset_class=AssetClass.OPTION
        )

        put_order = MarketOrderRequest(
            symbol=put.symbol,
            qty=quantity,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            asset_class=AssetClass.OPTION
        )

        trading_client.submit_order(call_order)
        trading_client.submit_order(put_order)

        total_cost = (call.price + put.price) * 100 * quantity
        return StraddlePosition(call, put, (call.implied_volatility + put.implied_volatility)/2, total_cost, quantity)

    except Exception as e:
        print(f"Execution Error: {str(e)}")
        return None

def monitor_positions(active_positions):
    while True:
        try:
            current_iv = get_current_iv("SPY")
            for pos in active_positions.copy():
                call_value = float(trading_client.get_position(pos.call_contract.symbol).market_value)
                put_value = float(trading_client.get_position(pos.put_contract.symbol).market_value)
                total_value = call_value + put_value
                pl_pct = (total_value - pos.entry_price) / pos.entry_price

                # Check exit conditions
                if abs(pl_pct) >= 0.5 or current_iv < (pos.entry_iv * 0.8):
                    print(f"Closing position: PL {pl_pct:.1%}, Current IV {current_iv:.2f}")
                    trading_client.close_position(pos.call_contract.symbol)
                    trading_client.close_position(pos.put_contract.symbol)
                    active_positions.remove(pos)

            time.sleep(30)

        except Exception as e:
            print(f"Monitoring Error: {str(e)}")
            time.sleep(60)

def main(symbol="SPY"):
    active_positions = []
    try:
        while True:
            account = trading_client.get_account()
            hv = get_historical_volatility(symbol)
            current_iv = get_current_iv(symbol)

            print(f"Strategy Check: HV {hv:.2f} | IV {current_iv:.2f}")

            # Entry condition
            if current_iv > hv * 1.2 and not active_positions:
                # Calculate position size
                equity = float(account.equity)
                straddle_price = (5 + 4.5) * 100  # Example prices, replace with actual
                max_quantity = min(int((equity * 0.02) / straddle_price), 1)

                if max_quantity > 0:
                    position = execute_straddle(symbol, max_quantity)
                    if position:
                        active_positions.append(position)
                        monitor_positions(active_positions)

            time.sleep(3600)  # Re-check hourly

    except KeyboardInterrupt:
        print("Bot stopped by user")
    except Exception as e:
        print(f"Fatal Error: {str(e)}")

if __name__ == "__main__":
    main()
