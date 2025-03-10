"""
Improved Alpaca Options Trading Script

This script demonstrates how to:
1. Retrieve option contracts with proper filtering
2. Execute both single-leg and multi-leg option strategies
3. Monitor positions and orders

Following Alpaca's recommended approach for options trading.
"""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import time
import numpy as np
import csv
import yfinance as yf

from alpaca.trading.client import TradingClient
from alpaca.data.historical.stock import StockHistoricalDataClient, StockLatestTradeRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.requests import (
    GetOptionContractsRequest,
    MarketOrderRequest,
    LimitOrderRequest,
    GetOrdersRequest,
    ClosePositionRequest,
    OptionLegRequest
)
from alpaca.trading.enums import (
    OrderSide,
    OrderType,
    TimeInForce,
    QueryOrderStatus,
    ContractType,
    AssetStatus,
    ExerciseStyle,
    OrderClass
)

# Import configuration
from config import ALPACA_CONFIG

# Initialize clients
trading_client = TradingClient(
    api_key=ALPACA_CONFIG["apiKey"],
    secret_key=ALPACA_CONFIG["secretKey"],
    paper=True
)

# Initialize stock data client for getting current prices
stock_data_client = StockHistoricalDataClient(
    api_key=ALPACA_CONFIG["apiKey"],
    secret_key=ALPACA_CONFIG["secretKey"]
)

def get_account_info():
    """Get account information including options approval level"""
    account = trading_client.get_account()
    print(f"Account ID: {account.id}")
    print(f"Cash: ${float(account.cash)}")
    
    # Fix attribute names
    options_level = getattr(account, 'options_trading_level', 'Not available')
    options_approved = getattr(account, 'options_approved_level', 'Not available')
    
    print(f"Options Trading Level: {options_level}")
    print(f"Options Approved Level: {options_approved}")
    return account

def get_current_price(symbol):
    """Get the current price of a stock"""
    try:
        request = StockLatestTradeRequest(symbol_or_symbols=symbol)
        response = stock_data_client.get_stock_latest_trade(request)
        current_price = response[symbol].price
        print(f"Current price of {symbol}: ${current_price:.2f}")
        return current_price
    except Exception as e:
        print(f"Error getting current price for {symbol}: {str(e)}")
        return None

def get_historical_prices(symbol, days=30):
    """Fetch historical prices for a given stock symbol using yfinance."""
    try:
        stock_data = yf.download(symbol, period=f'{days}d')
        if stock_data.empty:
            print(f"No historical data found for {symbol}")
            return []
        return stock_data['Close'].values.flatten()  # Flatten to 1D array
    except Exception as e:
        print(f"Error fetching historical prices for {symbol}: {str(e)}")
        return []

def get_historical_volatility(symbol, days=30):
    """Calculate historical volatility for a given stock symbol using yfinance."""
    try:
        historical_prices = get_historical_prices(symbol, days)
        if len(historical_prices) < 2:
            print(f"Not enough historical data for {symbol}")
            return None
        # Ensure historical_prices is a 1D array
        if historical_prices.ndim != 1:
            print(f"Unexpected shape for historical prices for {symbol}: {historical_prices.shape}")
            return None
        # Check for NaN values
        if np.any(np.isnan(historical_prices)):
            print(f"NaN values found in historical prices for {symbol}")
            return None
        returns = np.diff(historical_prices) / historical_prices[:-1]
        volatility = np.std(returns) * np.sqrt(252)  # Annualize
        print(f"Historical volatility for {symbol}: {volatility:.2f}")  # Debugging statement
        return volatility
    except Exception as e:
        print(f"Error calculating historical volatility for {symbol}: {str(e)}")
        return None

def get_current_iv(symbol):
    """Get the current implied volatility of a stock using yfinance."""
    try:
        option_chain = yf.Ticker(symbol).options
        if not option_chain:
            print(f"No option contracts found for {symbol}")
            return None
        # Fetch the first available option chain
        contracts = yf.Ticker(symbol).option_chain(option_chain[0])
        ivs = []
        for contract in contracts.calls.itertuples():
            if hasattr(contract, 'impliedVolatility') and contract.impliedVolatility is not None:
                ivs.append(contract.impliedVolatility)
        return np.mean(ivs) if ivs else None
    except Exception as e:
        print(f"Error fetching implied volatility for {symbol}: {str(e)}")
        return None

def get_option_contracts(symbol, days_min=7, days_max=30, contract_type=None):
    """
    Get option contracts for a symbol with expiration between min and max days
    
    Args:
        symbol: The underlying stock symbol
        days_min: Minimum days until expiration
        days_max: Maximum days until expiration
        contract_type: ContractType.CALL, ContractType.PUT, or None for both
    
    Returns:
        List of option contracts
    """
    print(f"\nFetching option contracts for {symbol}...")
    
    # Calculate date range for expiration
    now = datetime.now(tz=ZoneInfo("America/New_York"))
    min_expiry = now.date() + timedelta(days=days_min)
    max_expiry = now.date() + timedelta(days=days_max)
    
    print(f"Looking for contracts with expiration between {min_expiry} and {max_expiry}")
    
    try:
        # Create request for call contracts
        call_request = GetOptionContractsRequest(
            underlying_symbols=[symbol],
            status=AssetStatus.ACTIVE,
            expiration_date_gte=min_expiry,
            expiration_date_lte=max_expiry,
            type=ContractType.CALL
        )
        
        # Create request for put contracts
        put_request = GetOptionContractsRequest(
            underlying_symbols=[symbol],
            status=AssetStatus.ACTIVE,
            expiration_date_gte=min_expiry,
            expiration_date_lte=max_expiry,
            type=ContractType.PUT
        )
        
        # Get responses from API
        call_response = trading_client.get_option_contracts(call_request)
        put_response = trading_client.get_option_contracts(put_request)
        
        # Combine contracts from both responses
        contracts = call_response.option_contracts + put_response.option_contracts
        print(f"Found {len(contracts)} contracts")
        
        return contracts
    except Exception as e:
        print(f"Error fetching option contracts: {str(e)}")
        return []

def find_nearest_strike_contract(contracts, target_price, is_call=True, otm_only=True):
    """
    Find the contract with strike price closest to target price
    
    Args:
        contracts: List of option contracts
        target_price: Target price to find closest strike
        is_call: True for calls, False for puts
        otm_only: True to only find OTM options
    
    Returns:
        The option contract with closest strike to target price
    """
    if not contracts:
        return None
        
    closest_contract = None
    min_diff = float('inf')
    
    for contract in contracts:
        try:
            if contract.type != (ContractType.CALL if is_call else ContractType.PUT):
                continue
                
            strike = float(contract.strike_price)
            
            # Check if the option is OTM if otm_only is True
            if otm_only:
                if is_call and strike <= target_price:
                    continue  # Skip ITM calls
                if not is_call and strike >= target_price:
                    continue  # Skip ITM puts
            
            diff = abs(strike - target_price)
            if diff < min_diff:
                min_diff = diff
                closest_contract = contract
        except Exception as e:
            print(f"Error processing contract: {str(e)}")
            continue
    
    if closest_contract:
        print(f"Selected {'call' if is_call else 'put'}: {closest_contract.symbol}")
        print(f"Strike: {closest_contract.strike_price}, Expiration: {closest_contract.expiration_date}")
    else:
        print(f"No suitable {'call' if is_call else 'put'} contract found")
    
    return closest_contract

def find_suitable_contracts(symbol):
    """Find suitable call and put contracts for a straddle on the given symbol."""
    contracts = get_option_contracts(symbol)
    if not contracts:
        print(f"No contracts found for {symbol}")
        return None, None
    
    current_price = get_current_price(symbol)
    if current_price is None:
        print(f"Could not fetch current price for {symbol}. Exiting strategy.")
        return None, None
    
    call_contract = find_nearest_strike_contract(contracts, current_price, is_call=True)
    put_contract = find_nearest_strike_contract(contracts, current_price, is_call=False)
    
    if call_contract and put_contract:
        return call_contract, put_contract
    else:
        print(f"Could not find suitable contracts for straddle on {symbol}.")
        return None, None

def place_single_leg_order(contract, quantity=1, side=OrderSide.BUY, order_type=OrderType.MARKET):
    """
    Place an order for a single option contract
    
    Args:
        contract: The option contract
        quantity: Number of contracts
        side: OrderSide.BUY or OrderSide.SELL
        order_type: OrderType.MARKET or OrderType.LIMIT
    
    Returns:
        Order response
    """
    try:
        print(f"\nPlacing {side.name} order for {quantity} contract(s) of {contract.symbol}...")
        
        # Create and submit the order
        order_request = MarketOrderRequest(
            symbol=contract.symbol,
            qty=quantity,
            side=side,
            time_in_force=TimeInForce.DAY,
            type=order_type
        )
        
        order = trading_client.submit_order(order_request)
        print(f"Order placed successfully! Order ID: {order.id}")
        print(f"Status: {order.status}")
        return order
    except Exception as e:
        print(f"Error placing order: {str(e)}")
        return None

def place_straddle_order(call_contract, put_contract, quantity=1, order_type=OrderType.MARKET):
    """
    Place a straddle order (buy both call and put)
    
    Args:
        call_contract: The call option contract
        put_contract: The put option contract
        quantity: Number of contracts for each leg
        order_type: OrderType.MARKET or OrderType.LIMIT
    
    Returns:
        Order response
    """
    try:
        print(f"\nPlacing straddle order for {quantity} contract(s) each of:")
        print(f"Call: {call_contract.symbol}")
        print(f"Put: {put_contract.symbol}")
        
        # Create legs for the multi-leg order
        legs = [
            OptionLegRequest(
                symbol=call_contract.symbol,
                side=OrderSide.BUY,
                ratio_qty=1
            ),
            OptionLegRequest(
                symbol=put_contract.symbol,
                side=OrderSide.BUY,
                ratio_qty=1
            )
        ]
        
        # Create and submit the order
        order_request = MarketOrderRequest(
            qty=quantity,
            order_class=OrderClass.MLEG,  # Multi-leg order
            time_in_force=TimeInForce.DAY,
            type=order_type,
            legs=legs
        )
        
        order = trading_client.submit_order(order_request)
        print(f"Straddle order placed successfully! Order ID: {order.id}")
        print(f"Status: {order.status}")
        return order
    except Exception as e:
        print(f"Error placing straddle order: {str(e)}")
        return None

def get_positions():
    """Get all open positions"""
    try:
        positions = trading_client.get_all_positions()
        print(f"\nCurrent Positions ({len(positions)}):")
        for position in positions:
            print(f"Symbol: {position.symbol}")
            print(f"Quantity: {position.qty}")
            print(f"Cost Basis: ${float(position.cost_basis):.2f}")
            print(f"Market Value: ${float(position.market_value):.2f}")
            print(f"Unrealized P/L: ${float(position.unrealized_pl):.2f}")
            print("---")
        return positions
    except Exception as e:
        print(f"Error getting positions: {str(e)}")
        return []

def close_position(symbol):
    """Close a position by symbol"""
    print(f"Attempting to close position for {symbol}...")
    try:
        print(f"\nClosing position for {symbol}...")
        result = trading_client.close_position(symbol_or_asset_id=symbol)
        print(f"Position closed: {result}")
        return result
    except Exception as e:
        print(f"Error closing position for {symbol}: {str(e)}")
        return None

def check_orders_and_positions():
    """Check recent orders and positions"""
    try:
        # Get recent orders
        orders = trading_client.get_orders(
            GetOrdersRequest(
                status=QueryOrderStatus.ALL,
                limit=5
            )
        )
        
        print("\nRecent Orders:")
        for order in orders:
            print(f"Order ID: {order.id}")
            print(f"Symbol: {order.symbol}")
            print(f"Side: {order.side}")
            print(f"Quantity: {order.qty}")
            print(f"Status: {order.status}")
            print("---")
        
        # Get current positions
        positions = trading_client.get_all_positions()
        
        print("\nCurrent Positions:")
        for position in positions:
            print(f"Symbol: {position.symbol}")
            print(f"Quantity: {position.qty}")
            if hasattr(position, 'cost_basis'):
                print(f"Cost Basis: ${float(position.cost_basis):.2f}")
            if hasattr(position, 'market_value'):
                print(f"Market Value: ${float(position.market_value):.2f}")
            if hasattr(position, 'unrealized_pl'):
                print(f"Unrealized P/L: ${float(position.unrealized_pl):.2f}")
            print("---")
            
    except Exception as e:
        print(f"Error checking orders and positions: {str(e)}")

def manage_open_positions():
    """Monitor open positions and check for take profit/loss conditions"""
    while True:
        try:
            positions = get_positions()
            if not positions:
                print("No open positions to monitor.")
                time.sleep(60)  # Wait before checking again
                continue
            
            for position in positions:
                try:
                    # Use cost basis and market value for profit/loss calculations
                    cost_basis = float(position.cost_basis)
                    market_value = float(position.market_value)
                    
                    # Calculate take profit and stop loss prices
                    take_profit_price = cost_basis * 1.1  # 20% profit
                    stop_loss_price = cost_basis * 0.95  # 10% loss
                    
                    if market_value >= take_profit_price:
                        print(f"Taking profit on {position.symbol}. Market value: ${market_value:.2f}, Take profit price: ${take_profit_price:.2f}")
                        # Check if the position exists before closing
                        if position.symbol in [p.symbol for p in positions]:
                            print(f"Closing position: {position.symbol}")
                            close_position(position.symbol)
                        else:
                            print(f"Position {position.symbol} not found.")
                        corresponding_symbol = position.symbol.replace('C', 'P') if 'C' in position.symbol else position.symbol.replace('P', 'C')
                        # Check if the corresponding position exists before closing
                        if corresponding_symbol in [p.symbol for p in positions]:
                            print(f"Closing corresponding position: {corresponding_symbol}")
                            close_position(corresponding_symbol)
                        else:
                            print(f"Corresponding position {corresponding_symbol} not found.")
                    elif market_value <= stop_loss_price:
                        print(f"Stopping loss on {position.symbol}. Market value: ${market_value:.2f}, Stop loss price: ${stop_loss_price:.2f}")
                        # Check if the position exists before closing
                        if position.symbol in [p.symbol for p in positions]:
                            print(f"Closing position: {position.symbol}")
                            close_position(position.symbol)
                        else:
                            print(f"Position {position.symbol} not found.")
                        corresponding_symbol = position.symbol.replace('C', 'P') if 'C' in position.symbol else position.symbol.replace('P', 'C')
                        # Check if the corresponding position exists before closing
                        if corresponding_symbol in [p.symbol for p in positions]:
                            print(f"Closing corresponding position: {corresponding_symbol}")
                            close_position(corresponding_symbol)
                        else:
                            print(f"Corresponding position {corresponding_symbol} not found.")
                except Exception as e:
                    print(f"Error managing position for {position.symbol}: {str(e)}")
            
            # Check for new straddle opportunities
            symbols = ["SPY", "QQQ", "IWM", "NVDA", "PLTR", "RDDT", "LUNR", "TSLA"]  # Add more symbols as needed
            for symbol in symbols:
                execute_volatility_straddle(symbol)
        except Exception as e:
            print(f"Error in managing positions: {str(e)}")
        
        time.sleep(30)  # Wait before checking positions again

import pytz
from datetime import datetime

def is_market_open():
    eastern = pytz.timezone('US/Eastern')
    current_time = datetime.now(eastern)
    market_open_time = current_time.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close_time = current_time.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open_time <= current_time <= market_close_time

def execute_volatility_straddle(symbol):
    # Check if a straddle position already exists for this symbol
    positions = get_positions()
    for position in positions:
        if position.symbol.startswith(symbol):
            print(f"Straddle position already exists for {symbol}. Skipping...")
            return
    
    historical_iv = get_historical_volatility(symbol)
    current_iv = get_current_iv(symbol)

    if historical_iv is None or current_iv is None:
        print(f"Could not calculate volatilities for {symbol}")
        return

    # Compare current IV with historical IV
    if current_iv > 1.2 * historical_iv:
        print(f"Entering straddle for {symbol}: Current IV ({current_iv:.2f}) is greater than 1.2 times Historical IV ({historical_iv:.2f})")
        
        # Get current price
        current_price = get_current_price(symbol)
        if current_price is None:
            print(f"Could not fetch current price for {symbol}. Exiting strategy.")
            return

        # Define call_contract and put_contract before checking their values
        call_contract, put_contract = find_suitable_contracts(symbol)
        
        if call_contract and put_contract:
            if not is_market_open():
                print(f"Market is closed. Cannot place orders for {symbol}.")
                return
            # Calculate position size based on available buying power and cost of contracts
            account_info = get_account_info()
            options_buying_power = float(account_info.options_buying_power)  # Ensure options_buying_power is a float
            print(f"Options buying power: {options_buying_power}")  # Debugging statement
            cost_basis = (float(call_contract.close_price) + float(put_contract.close_price)) * 2  # Total cost for one straddle
            max_contracts = int(options_buying_power // cost_basis)
            position_size = min(max_contracts, 1)  # Limit to 1 straddle

            # Place straddle order
            place_straddle_order(call_contract, put_contract, quantity=position_size)
            
            # Set take profit and stop loss
            take_profit_price = current_price * 1.1  # 10% profit
            stop_loss_price = current_price * 0.95  # 5% loss
            print(f"Take profit set at ${take_profit_price:.2f}, Stop loss set at ${stop_loss_price:.2f}")
        else:
            print(f"Could not find suitable contracts for straddle on {symbol}.")
    else:
        print(f"Entry condition not met for {symbol}: Current IV ({current_iv:.2f}) <= 1.2 * Historical IV ({historical_iv:.2f})")

def weighted_volatility(mid_price, volume):
    average = np.average(mid_price, weights=volume)
    variance = np.average((mid_price - average) ** 2, weights=volume)
    return np.sqrt(variance)

def compute_volatility(api, start_date, end_date, ticker="AAPL", verbose=True):
    volatility_dict = dict()
    current_day = start_date
    delta = datetime.timedelta(days=1)

    while current_day <= end_date:
        current_day_str = current_day.strftime("%Y-%m-%d")
        if not np.is_busday(current_day_str):
            current_day += delta
            if verbose:
                print("Skipping " + current_day_str)
            continue

        start_day = current_day + datetime.timedelta(hours=9, minutes=30)
        end_day = current_day + datetime.timedelta(hours=16)

        start_day_str = start_day.strftime("%Y-%m-%dT%H:%M:%S-04:00")
        end_day_str = end_day.strftime("%Y-%m-%dT%H:%M:%S-04:00")

        barset = api.get_barset(ticker, "minute", start=start_day_str, end=end_day_str)
        parsed_bar = barset[ticker]

        if len(parsed_bar) == 0:
            current_day += delta
            if verbose:
                print("Skipping " + current_day_str)
            continue

        if verbose:
            print("Processing " + current_day_str)

        mid_price = [np.average([x.h, x.l]) for x in parsed_bar]
        volume = [x.v for x in parsed_bar]

        volatility = weighted_volatility(mid_price, volume)
        volatility_dict[current_day_str] = volatility

        current_day += delta

    return volatility_dict

def main():
    symbols = ["SPY", "QQQ", "IWM", "NVDA", "PLTR", "RDDT", "LUNR", "TSLA"]  # Add more symbols as needed
    for symbol in symbols:
        historical_iv = get_historical_volatility(symbol)
        current_iv = get_current_iv(symbol)
        print(f"Current IV for {symbol}: {current_iv}")

        # Call the straddle execution function
        execute_volatility_straddle(symbol)

    # Start managing open positions
    manage_open_positions()

if __name__ == "__main__":
    main()
    
    # Ask if user wants to check positions after the main flow
    check_positions = input("\nCheck current orders and positions? (y/n): ")
    if check_positions.lower() == 'y':
        check_orders_and_positions()
