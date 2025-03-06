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

from alpaca.trading.client import TradingClient
from alpaca.data.historical.stock import StockHistoricalDataClient, StockLatestTradeRequest
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

def get_current_iv(symbol):
    """Get the current implied volatility of a stock"""
    try:
        # TO DO: implement current IV retrieval
        return 0.5  # placeholder value
    except Exception as e:
        print(f"Error getting current IV for {symbol}: {str(e)}")
        return None

def get_historical_volatility(symbol):
    """Get the historical volatility of a stock"""
    try:
        # TO DO: implement historical IV retrieval
        return 0.3  # placeholder value
    except Exception as e:
        print(f"Error getting historical volatility for {symbol}: {str(e)}")
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
        # Create request for option contracts
        request = GetOptionContractsRequest(
            underlying_symbols=[symbol],
            status=AssetStatus.ACTIVE,
            expiration_date_gte=min_expiry,
            expiration_date_lte=max_expiry,
            type=contract_type
        )
        
        # Get response from API
        response = trading_client.get_option_contracts(request)
        
        # Convert response to a list
        contracts = response.option_contracts
        print(f"Found {len(contracts)} contracts")
        
        # Print details for the first few contracts
        for i, contract in enumerate(contracts[:5]):
            if i == 0:
                print("\nSample contract details:")
                
            print(f"Symbol: {contract.symbol}")
            print(f"Type: {contract.type}")
            print(f"Strike: {contract.strike_price}")
            print(f"Expiration: {contract.expiration_date}")
            if hasattr(contract, 'open_interest'):
                print(f"Open Interest: {contract.open_interest}")
            print("---")
        
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
    try:
        print(f"\nClosing position for {symbol}...")
        result = trading_client.close_position(symbol_or_asset_id=symbol)
        print(f"Position closed: {result}")
        return result
    except Exception as e:
        print(f"Error closing position: {str(e)}")
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
                take_profit_price = cost_basis * 1.2  # 20% profit
                stop_loss_price = cost_basis * 0.9  # 10% loss
                
                if market_value >= take_profit_price:
                    print(f"Taking profit on {position.symbol}. Market value: ${market_value:.2f}, Take profit price: ${take_profit_price:.2f}")
                    close_position(position.symbol)
                elif market_value <= stop_loss_price:
                    print(f"Stopping loss on {position.symbol}. Market value: ${market_value:.2f}, Stop loss price: ${stop_loss_price:.2f}")
                    close_position(position.symbol)
                
            except Exception as e:
                print(f"Error managing position for {position.symbol}: {str(e)}")
        
        time.sleep(60)  # Wait before checking positions again

def execute_volatility_straddle(symbol):
    # Check if a straddle position already exists for this symbol
    positions = get_positions()
    for position in positions:
        if position.symbol.startswith(symbol):
            print(f"Straddle position already exists for {symbol}. Skipping...")
            return
    
    # Get current price
    current_price = get_current_price(symbol)
    if current_price is None:
        print(f"Could not fetch current price for {symbol}. Exiting strategy.")
        return
    
    # Get current implied volatility
    current_iv = get_current_iv(symbol)
    if current_iv is None:
        print(f"Could not fetch current implied volatility for {symbol}. Exiting strategy.")
        return
    
    # Get historical volatility
    historical_iv = get_historical_volatility(symbol)
    if historical_iv is None:
        print(f"Could not fetch historical volatility for {symbol}. Exiting strategy.")
        return
    
    # Entry condition: Current IV > 1.2 * Historical IV
    if current_iv > 1.2 * historical_iv:
        print(f"Entry condition met for {symbol}: Current IV ({current_iv:.2f}) > 1.2 * Historical IV ({historical_iv:.2f})")
        
        # Calculate position size (2% of total capital)
        account_info = get_account_info()
        capital = float(account_info.cash)
        position_size = min(1, (0.02 * capital) // (current_price * 2))  # 1 straddle = 2 contracts
        
        # Get option contracts
        contracts = get_option_contracts(symbol)
        call_contract = find_nearest_strike_contract(contracts, current_price, is_call=True)
        put_contract = find_nearest_strike_contract(contracts, current_price, is_call=False)
        
        if call_contract and put_contract:
            # Place straddle order
            place_straddle_order(call_contract, put_contract, quantity=int(position_size))
            
            # Set take profit and stop loss
            take_profit_price = current_price * 1.2  # 20% profit
            stop_loss_price = current_price * 0.9  # 10% loss
            print(f"Take profit set at ${take_profit_price:.2f}, Stop loss set at ${stop_loss_price:.2f}")
        else:
            print(f"Could not find suitable contracts for straddle on {symbol}.")
    else:
        print(f"Entry condition not met for {symbol}: Current IV ({current_iv:.2f}) <= 1.2 * Historical IV ({historical_iv:.2f})")

def main():
    symbols = ["SPY", "QQQ", "IWM"]  # Add more symbols as needed
    for symbol in symbols:
        execute_volatility_straddle(symbol)
    
    # Start managing open positions
    manage_open_positions()

if __name__ == "__main__":
    main()
    
    # Ask if user wants to check positions after the main flow
    check_positions = input("\nCheck current orders and positions? (y/n): ")
    if check_positions.lower() == 'y':
        check_orders_and_positions()
