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

def main():
    """Main function to execute options trading"""
    # Check account information
    account = get_account_info()
    
    # Choose a symbol to trade
    symbol = "SPY"  # S&P 500 ETF
    
    # Get current price
    current_price = get_current_price(symbol)
    if not current_price:
        print("Cannot proceed without current price")
        return
    
    # Get option contracts
    print("\nGetting call options...")
    call_contracts = get_option_contracts(symbol, days_min=5, days_max=60, contract_type=ContractType.CALL)
    
    print("\nGetting put options...")
    put_contracts = get_option_contracts(symbol, days_min=5, days_max=60, contract_type=ContractType.PUT)
    
    if not call_contracts or not put_contracts:
        print("Not enough contracts to proceed")
        return
    
    # Find suitable contracts for a straddle (closest ATM call and put)
    call_contract = find_nearest_strike_contract(call_contracts, current_price, is_call=True, otm_only=False)
    put_contract = find_nearest_strike_contract(put_contracts, current_price, is_call=False, otm_only=False)
    
    if not call_contract or not put_contract:
        print("Could not find suitable contracts for straddle")
        return
    
    # Ask if user wants to place orders
    print("\nOptions Strategy: Long Straddle")
    print(f"Call: {call_contract.symbol} - Strike: {call_contract.strike_price}")
    print(f"Put: {put_contract.symbol} - Strike: {put_contract.strike_price}")
    
    action = input("\nWhat would you like to do? (straddle/call/put/positions/exit): ").lower()
    
    if action == "straddle":
        # Place straddle order
        quantity = int(input("Enter quantity (default 1): ") or 1)
        place_straddle_order(call_contract, put_contract, quantity)
    elif action == "call":
        # Place call order only
        quantity = int(input("Enter quantity (default 1): ") or 1)
        place_single_leg_order(call_contract, quantity)
    elif action == "put":
        # Place put order only
        quantity = int(input("Enter quantity (default 1): ") or 1)
        place_single_leg_order(put_contract, quantity)
    elif action == "positions":
        # Show positions
        positions = get_positions()
        if positions:
            symbol_to_close = input("\nEnter symbol to close position (or enter to skip): ")
            if symbol_to_close:
                close_position(symbol_to_close)
    else:
        print("Exiting without placing orders")

if __name__ == "__main__":
    main()
    
    # Ask if user wants to check positions after the main flow
    check_positions = input("\nCheck current orders and positions? (y/n): ")
    if check_positions.lower() == 'y':
        check_orders_and_positions()
