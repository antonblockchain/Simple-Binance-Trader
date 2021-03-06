#! /usr/bin/env python3

'''
trader

'''
import os
import sys
import time
import logging
import datetime
import threading
import trader_configuration as TC


## Base commission fee with binance.
COMMISION_FEE = 0.00075

## Supported market.
SUPPORTED_MARKETS = ['SPOT', 'MARKET']

## Base layout used by the trader.
BASE_MARKET_LAYOUT = {
    'can_order':True,       # If the bot is able to trade in the current market.
    'buy_price':0.0,        # The price related to BUY.
    'sell_price':0.0,       # The price related to SELL.
    'buy_time':0,           # Time of when the trader bought.
    'sell_time':0,
    'market_status':None,   # The current status tied to the trader.
    'currency_left':0.0,    # The amount of currenty left from the base.
    'tokens_holding':0.0,   # Amount of tokens being held.
    'current_stage':0,      # User to inform about the current order stage.
    'order_id':             # The ID that is tied to the placed order.
        {'B':None,'S':None},
    'order_type':           # The type of the order that is placed
        {'B':'WAIT', 'S':None},
    'order_status':         # The status of the current order.
        {'B':None, 'S':None},
    'order_description':    # The description of the order.
        {'B':None, 'S':None}
}

## Market extra required data.
TYPE_MARKET_EXTRA = {
    'loan_cost':0,
    'loan_id':None,
}


class BaseTrader(object):

    def __init__(self, quote_asset, base_asset, rest_api, socket_api=None, data_if=None, logs_dir=None):
        '''
        Initilize the trader object and setup all the dataobjects that will be used by the trader object.
        '''
        symbol = '{0}{1}'.format(base_asset, quote_asset)

        ## Easy printable format for market symbol.
        self.print_pair = '{0}-{1}'.format(quote_asset, base_asset)
        self.quote_asset = quote_asset
        self.base_asset = base_asset

        logging.info('[BaseTrader][{0}] Initilizing trader object and empty attributes.'.format(self.print_pair))

        ## Sets the rest api that will be used by the trader.
        self.rest_api = rest_api

        if socket_api == None and data_if == None:
            logging.critical('[BaseTrader][{0}] Initilization failed, bot must have either socket_api OR data_if set.'.format(self.print_pair))
            return

        if socket_api:
            self.candle_enpoint = socket_api.get_live_candles
            self.depth_endpoint = socket_api.get_live_depths
            self.socket_api = socket_api
            self.data_if = None
        else:
            self.socket_api = None
            self.data_if = data_if
            self.candle_enpoint = data_if.get_candle_data
            self.depth_endpoint = data_if.get_depth_data

        ## Setup the default path for the trader by market beeing traded.
        if logs_dir != None:
            self.orders_log_path = '{0}order_{1}_log.txt'.format(logs_dir, symbol)

        ## Configuration settings are held here:
        self.configuration = {}
        
        ## Market price action is held here:
        self.market_prices = {}

        ## Wallet data is stored here:
        self.wallet_pair = None

        ## Custom conditional parameters are held here:
        self.custom_conditional_data = {}

        # Here the indicators are stored.
        self.indicators = {}

        ## Here long market activity is recorded:
        self.long_position = {}

        ## Here short market activity is recorded:
        self.short_position = {}

        ## Here all buy/sell trades will be stored
        self.trade_recorder = []

        ## Data thats used to inform about the trader:
        self.state_data = {}

        ## Market rules are set here:
        self.rules = {}

        logging.debug('[BaseTrader][{0}] Initilized trader object.'.format(self.print_pair))


    def setup_initial_values(self, market_type, run_type, filters):
        '''
        Initilize the values for all of the trader objects.
        '''
        logging.info('[BaseTrader][{0}] Initilizing trader object attributes with data.'.format(self.print_pair))

        symbol = '{0}{1}'.format(self.base_asset, self.quote_asset)

        btc_base_pair = True if self.base_asset == 'BTC' else False

        ## Set intial values for the trader configuration.
        self.configuration.update({
            'market_type':market_type,
            'run_type':run_type,
            'trade_only_one':True,
            'base_asset':self.base_asset,
            'quote_asset':self.quote_asset,
            'symbol':symbol,
            'btc_base_pair':btc_base_pair
        })

        ## Set initial values for market price.
        self.market_prices.update({
            'lastPrice':0,
            'askPrice':0,
            'bidPrice':0
        })

        ## Set initial values for state data.
        self.state_data.update({
            'base_mac':0.0,         # The base mac value used as referance.
            'force_sell':False,     # If the trader should dump all tokens.
            'runtime_state':None,   # The state that actual trader object is at.
            'last_update_time':0    # The last time a full look of the trader was completed.
        })

        self.rules.update(filters)

        self.long_position.update(BASE_MARKET_LAYOUT)

        if market_type == 'MARGIN':
            self.short_position.update(BASE_MARKET_LAYOUT, TYPE_MARKET_EXTRA)
            self.long_position.update(TYPE_MARKET_EXTRA)

        logging.debug('[BaseTrader][{0}] Initilized trader attributes with data.'.format(self.print_pair))


    def start(self, MAC, wallet_pair):
        '''
        Start the trader.
        Requires: MAC (Max Allowed Currency, the max amount the trader is allowed to trade with in BTC).

        -> Check for previous trade.
            If a recent, not closed traded is seen, or leftover currency on the account over the min to place order then set trader to sell automatically.
        
        ->  Start the trader thread. 
            Once all is good the trader will then start the thread to allow for the market to be monitored.
        '''
        logging.info('[BaseTrader][{0}] Starting the trader object.'.format(self.print_pair))
        sock_symbol = self.base_asset+self.quote_asset

        if self.socket_api != None:
            while True:
                if self.socket_api.get_live_candles(sock_symbol) and ('a' in self.socket_api.get_live_depths(sock_symbol)):
                    break

        self.state_data['runtime_state'] = 'SETUP'
        self.wallet_pair = wallet_pair
        self.state_data['base_mac'] = float(MAC)
        
        self.long_position['currency_left'] = float(MAC)
        if self.short_position != {}:
            self.short_position['currency_left'] = float(MAC)

        ## Start the main of the trader in a thread.
        trader_thread = threading.Thread(target=self._main)
        trader_thread.start()
        return(True)


    def stop(self):
        ''' 
        Stop the trader.

        -> Trader cleanup.
            To gracefully stop the trader and cleanly eliminate the thread as well as market orders.
        '''
        logging.debug('[BaseTrader][{0}] Stopping trader.'.format(self.print_pair))

        if self.long_position['order_type']['S'] == None:
            self.long_position['order_status']['B'] = 'FORCE_PREVENT_BUY'

        if self.short_position['order_type']['S'] == None:
            self.short_position['order_status']['B'] = 'FORCE_PREVENT_BUY'

        while True:
            if self.long_position['order_type']['S'] == None and self.short_position['order_type']['S'] == None:
                break
            time.sleep(10)

        self.state_data['runtime_state'] = 'STOP'
        return(True)


    def _main(self):
        '''
        Main body for the trader loop.

        -> Wait for candle data to be fed to trader.
            Infinite loop to check if candle has been populated with data,

        -> Call the updater.
            Updater is used to re-calculate the indicators as well as carry out timed checks.

        -> Call Order Manager.
            Order Manager is used to check on currently PLACED orders.

        -> Call Trader Manager.
            Trader Manager is used to check the current conditions of the indicators then set orders if any can be PLACED.
        '''
        sock_symbol = self.base_asset+self.quote_asset
        last_wallet_update_time = 0

        if self.configuration['market_type'] == 'SPOT':
            position_types = ['LONG']
        elif self.configuration['market_type'] == 'MARGIN':
            position_types = ['LONG', 'SHORT']

        while self.state_data['runtime_state'] != 'STOP':
            ## Call the update function for the trader.
            candles = self.candle_enpoint(sock_symbol)
            books_data = self.depth_endpoint(sock_symbol)
            indicators = TC.technical_indicators(candles)
            self.indicators = indicators
            logging.debug('[BaseTrader][{0}] Collected trader data.'.format(self.print_pair))

            if self.configuration['run_type'] == 'REAL':
                socket_buffer_global = self.socket_api.socketBuffer

                if sock_symbol in self.socket_api.socketBuffer:
                    socket_buffer_symbol = self.socket_api.socketBuffer[sock_symbol]
                else:
                    socket_buffer_symbol = None

                ## Pull account balance data via the SPOT user data stream endpoint to update wallet pair information.
                if 'outboundAccountInfo' in socket_buffer_global:
                    if last_wallet_update_time != socket_buffer_global['outboundAccountInfo']['E']:
                        self.wallet_pair, last_wallet_update_time = self.update_wallets(socket_buffer_global)
            else:
                socket_buffer_symbol = None
                            
            self.market_prices = {
                'lastPrice':candles[0][4],
                'askPrice':books_data['a'][0][0],
                'bidPrice':books_data['b'][0][0]}

            if not self.state_data['runtime_state'] in ['STANDBY', 'FORCE_STANDBY', 'FORCE_PAUSE', 'SETUP']:
                ## Call for custom conditions that can be used for more advanced managemenet of the trader.

                for ptype in position_types:
                    self.custom_conditional_data, cp = TC.other_conditions(
                        self.custom_conditional_data, 
                        self.long_position if ptype == 'LONG' else self.short_position,
                        ptype,
                        candles,
                        indicators, 
                        self.configuration['symbol'],
                        self.configuration['btc_base_pair'])

                    ## logic to force only short or long to be activly traded, both with still monitor passivly tho.
                    if self.configuration['trade_only_one'] and self.configuration['market_type'] == 'MARGIN':
                        if (self.long_position['order_type']['B'] != 'WAIT' or self.long_position['order_type']['S'] != None) and ptype == 'SHORT':
                            continue
                        elif (self.short_position['order_type']['B'] != 'WAIT' or self.short_position['order_type']['S'] != None) and ptype == 'LONG':
                            continue

                    logging.debug('[BaseTrader][{0}] Checking for {1}'.format(self.print_pair, ptype))

                    ## For managing active orders.
                    if socket_buffer_symbol != None or self.configuration['run_type'] == 'TEST':
                        cp = self._order_status_manager(ptype, cp, socket_buffer_symbol)

                    ## For managing the placement of orders/condition checking.
                    if cp['can_order'] == True:
                        if self.state_data['runtime_state'] == 'RUN' and cp['market_status'] == 'TRADING':
                            tm_data = self._trade_manager(ptype, cp, indicators, candles)
                            cp = tm_data if tm_data else cp

                    if not cp['market_status']: 
                        cp['market_status'] = 'TRADING'

                    if ptype == 'long': self.long_position = cp
                    else: self.short_position = cp

                    time.sleep(.8)

            current_localtime = time.localtime()
            self.state_data['last_update_time'] = '{0}:{1}:{2}'.format(current_localtime[3], current_localtime[4], current_localtime[5])

            if self.state_data['runtime_state'] == 'SETUP':
                self.state_data['runtime_state'] = 'RUN'
        

    def _order_status_manager(self, ptype, cp, socket_buffer_symbol):
        '''
        This is the manager for all and any active orders.

        -> Check orders (Test/Real).
            This checks both the buy and sell side for test orders and updates the trader accordingly.

        -> Monitor trade outcomes.
            Monitor and note down the outcome of trades for keeping track of progress.
        '''
        active_trade = False
        trade_done = False
        
        if self.configuration['run_type'] == 'REAL':
            # Manage order reports sent via the socket.
            if 'executionReport' in socket_buffer_symbol:
                order_seen = socket_buffer_symbol['executionReport']

                all_active_orders = [self.long_position['order_id']['B'], self.long_position['order_id']['S']]
                if self.configuration['market_type'] == 'MARGIN':
                    all_active_orders+=[self.short_position['order_id']['B'], self.short_position['order_id']['S']]

                if not(order_seen['i'] in all_active_orders):
                    # Blocked is used to allow force buys.
                    if ptype == 'LONG':
                        active_trade = True
                else:
                    # Manage trader placed orders via order ID's to prevent order conflict.
                    all_mt_trades = [cp['order_id']['B'], cp['order_id']['S']]
                    if order_seen['i'] in all_mt_trades:
                        active_trade = True
        else:
            # Basic update for test orders.
            if cp['order_status']['B'] == 'PLACED' or cp['order_status']['S'] == 'PLACED':
                active_trade = True
                order_seen = None

        if active_trade:
            # Determine the current state of an order.
            side = 'BUY' if cp['order_type']['S'] == None else 'SELL'
            cp, trade_done, tokens_bought = self._check_active_trade(side, ptype, cp, order_seen)

        ## Monitor trade outcomes.
        if trade_done:
            print('Finished {0} trade for {1}'.format(side, self.print_pair))

            if side == 'BUY':
                # Here all the necissary variables and values are added to signal a completion on a buy trade.
                cp['tokens_holding'] = tokens_bought
                cp['buy_time'] = time.time()
                logging.info('[BaseTrader][{0}] Completed buy order.'.format(self.print_pair))

            elif side == 'SELL':
                # Here all the necissary variables and values are added to signal a completion on a sell trade.
                tokens_holding = cp['tokens_holding']
                fee = tokens_holding*COMMISION_FEE

                sellTime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))
                buyTime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(cp['buy_time']))

                if self.configuration['market_type']  == 'MARGIN':
                    if self.configuration['run_type'] == 'REAL' and cp['loan_cost'] != 0:
                        loan_repay_result = self.rest_api.repay_loan(asset=self.base_asset, amount=cp['loan_cost'])

                if self.rules['isFiat'] == True and self.rules['invFiatToBTC']:
                    outcome = float('{0:.8f}'.format(((cp['sell_price']-cp['buy_price'])*(tokens_holding/cp['sell_price']))))
                else: 
                    outcome = float('{0:.8f}'.format(((cp['sell_price']-cp['buy_price'])*tokens_holding)))

                print(self.orders_log_path)

                if self.orders_log_path != None:
                    with open(self.orders_log_path, 'a') as file:
                        buyResp = 'marketType:{3}, Buy order, price: {0:.8f}, time: {1} [{2}] | '.format(cp['buy_price'], buyTime, cp['order_description']['B'], ptype)
                        sellResp = 'Sell order, price: {0:.8f}, time: {1} [{2}], outcome: {3:.8f} [{4}]'.format(cp['sell_price'], sellTime, cp['order_description']['S'], outcome, self.configuration['symbol'])
                        file.write(buyResp+sellResp+'\n')
                        file.close()

                self.trade_recorder.append([cp['buy_price'], buyTime, cp['sell_price'], sellTime, outcome, ptype])

                cp['sell_time'] = time.time()
                cp['market_status'] = 'COMPLETE_TRADE'
                logging.info('[BaseTrader][{0}] Completed sell order.'.format(self.print_pair))
            return(self._setup_market(side, cp))
        else:
            return(cp)


    def _check_active_trade(self, side, ptype, cp, order_seen):
        trade_done = False
        tokens_bought = None
            
        if side == 'BUY':
            if self.configuration['run_type'] == 'REAL':
                if order_seen['S'] == 'BUY' or (ptype == 'SHORT' and order_seen['S'] == 'SELL'):
                    cp['buy_price'] = float(order_seen['L'])

                    if ptype == 'LONG':
                        target_wallet = self.base_asset
                        target_quantity = float(order_seen['q'])
                    elif ptype == 'SHORT':
                        target_wallet = self.quote_asset
                        target_quantity = float(order_seen['q'])*float(order_seen['L'])

                    if order_seen['X'] == 'FILLED' and target_wallet in self.wallet_pair:
                        wallet_pair = self.wallet_pair
                        if wallet_pair[target_wallet][0] >= target_quantity:
                            trade_done = True
                            tokens_bought = wallet_pair[self.base_asset][0]
                    elif order_seen['X'] == 'PARTIALLY_FILLED' and cp['order_status']['B'] != 'LOCKED':
                        cp['order_status']['B'] = 'LOCKED'
            else:
                if ptype == 'LONG':
                    if cp['buy_price'] <= self.market_prices['lastPrice']:
                        trade_done = True
                        tokens_bought = cp['tokens_holding']
                elif ptype == 'SHORT':
                    if cp['buy_price'] >= self.market_prices['lastPrice']:
                        trade_done = True
                        tokens_bought = cp['tokens_holding']

        elif side == 'SELL':
            if self.configuration['run_type'] == 'REAL':
                if order_seen['S'] == 'SELL' or (ptype == 'SHORT' and order_seen['S'] == 'BUY'):
                    if order_seen['X'] == 'FILLED':
                        cp['sell_price'] = float(order_seen['L'])
                        trade_done = True
                    elif order_seen['X'] == 'PARTIALLY_FILLED' and cp['order_status']['S'] != 'LOCKED':
                        cp['order_status']['S'] = 'LOCKED'
            else:
                if ptype == 'LONG':
                    if cp['sell_price'] >= self.market_prices['lastPrice']:
                        trade_done = True
                elif ptype == 'SHORT':
                    if cp['sell_price'] <= self.market_prices['lastPrice']:
                        trade_done = True
        return(cp, trade_done, tokens_bought)


    def _trade_manager(self, ptype, cp, indicators, candles):
        ''' 
        Here both the sell and buy conditions are managed by the trader.

        -> Manager Sell Conditions.
            Manage the placed sell condition as well as monitor conditions for the sell side.

        -> Manager Buy Conditions.
            Manage the placed buy condition as well as monitor conditions for the buy side.

        -> Place Market Order.
            Place orders on the market with real and assume order placemanet with test.
        '''
        updateOrder = False
        order = None

        if cp['order_type']['S'] and cp['order_status']['S'] != 'LOCKED':
            ## Manage SELL orders/check conditions.
            logging.debug('[BaseTrader][{0}] Checking for Sell condition.'.format(self.print_pair))
            exit_conditions = TC.long_exit_conditions if ptype == 'LONG' else TC.short_exit_conditions

            new_order = exit_conditions(
                self.custom_conditional_data,
                cp,
                indicators, 
                self.market_prices,
                candles,
                self.print_pair,
                self.configuration['btc_base_pair'])

            if not(new_order):
                return

            orderType = new_order['order_type']

            if orderType != 'WAIT':
                cp['order_description']['S'] = new_order['description']
                if self.configuration['run_type'] == 'TEST' and new_order['ptype'] == 'MARKET':
                    updateOrder = True
                elif 'price' in new_order:
                    if 'price' in new_order:
                        new_order['price'] = '{0:.{1}f}'.format(float(new_order['price']), self.rules['TICK_SIZE'])
                        if 'stopPrice' in new_order:
                            new_order['stopPrice'] = '{0:.{1}f}'.format(float(new_order['stopPrice']), self.rules['TICK_SIZE'])
                    
                    if float(new_order['price']) != float(cp['sell_price']):
                        updateOrder = True

            if cp['order_type']['S'] != orderType or updateOrder:
                if orderType == 'SIGNAL':
                    # If SIGNAL is set then place a order.
                    order = new_order
                elif orderType == 'STOP_LOSS':
                    # If STOP_LOSS is set then place a stop loss.
                    order = new_order
                elif orderType == 'WAIT':
                    # If WAIT is set then remove all orders and change order type to wait.
                    self._cancel_order(cp['order_id']['S'])
                    cp['order_status']['S'] = None
                    cp['order_type']['S'] = 'WAIT'
                else:
                    logging.critical('[BaseTrader][{0}] The order type [{1}] is not currently available.'.format(self.print_pair, orderType))

        elif cp['order_type']['B'] and cp['order_status']['B'] != 'LOCKED' and self.state_data['runtime_state'] != 'FORCE_PREVENT_BUY':
            ## Manage BUY orders/check conditions.
            logging.debug('[BaseTrader][{0}] Checking for Buy condition.'.format(self.print_pair))
            entry_conditions = TC.long_entry_conditions if ptype == 'LONG' else TC.short_entry_conditions

            new_order = entry_conditions(
                self.custom_conditional_data,
                cp,
                indicators, 
                self.market_prices,
                candles,
                self.print_pair,
                self.configuration['btc_base_pair'])

            if not(new_order):
                return

            if type(new_order) == tuple:
                stage = new_order[1]
                new_order = new_order[0]

                if cp['current_stage'] != stage and stage != 0:
                    cp['current_stage'] = stage
                    print('Market {0} at type {1} is at stage {2}'.format(self.configuration['symbol'], ptype, str(stage)))

            orderType = new_order['order_type']

            if orderType != 'WAIT':
                cp['order_description']['B'] = new_order['description']
                if self.configuration['run_type'] == 'TEST' and new_order['ptype'] == 'MARKET':
                    updateOrder = True
                elif 'price' in new_order:
                    if 'price' in new_order:
                        new_order['price'] = '{0:.{1}f}'.format(float(new_order['price']), self.rules['TICK_SIZE'])
                        if 'stopPrice' in new_order:
                            new_order['stopPrice'] = '{0:.{1}f}'.format(float(new_order['stopPrice']), self.rules['TICK_SIZE'])
                    
                    if float(new_order['price']) != float(cp['buy_price']):
                        updateOrder = True

            if cp['order_type']['B'] != orderType or updateOrder:
                if orderType == 'SIGNAL':
                    # If SIGNAL is set then place a order.
                    order = new_order
                elif orderType == 'WAIT':
                    # If WAIT is set then remove all orders and change order type to wait.
                    self._cancel_order(cp['order_id']['B'])
                    cp['order_status']['B'] = None
                    cp['order_type']['B'] = 'WAIT'
                else:
                    logging.critical('[BaseTrader][{0}] The order type [{1}] is not currently available.'.format(self.print_pair, orderType))

        ## Place Market Order.
        if order:
            order_results = self._place_order(ptype, cp, order)
            logging.debug('order: {0}\norder result:\n{1}'.format(order, order_results))

            if order_results != None:
                print(order_results)
                order_results = order_results['data']
                logging.info('[BaseTrader][{0}] Order placed for {1}.'.format(self.print_pair, orderType))
                logging.debug('[BaseTrader][{0}] Order placement results:\n{1}'.format(self.print_pair, str(order_results)))

                if 'type' in order_results:
                    if order_results['type'] == 'MARKET':
                        price1 = order_results['fills'][0]['price']
                    else:
                        price1 = order_results['price']
                else: price1 = None

                price2 = None
                if 'price' in order:
                    price2 = float(order['price'])
                    if price1 == 0.0 or price1 == None: 
                        order_price = price2
                    else: order_price = price1
                else: order_price = price1

                if order['side'] == 'BUY':
                    cp['buy_price'] = order_price

                    if self.configuration['run_type'] == 'REAL':
                        cp['order_id']['B'] = order_results['orderId']

                        if self.configuration['market_type'] == 'MARGIN':
                            if 'loan_id' in order_results:
                                cp['load_id'] = order_results['load_id'] 
                                cp['loan_cost'] = order_results['loan_cost']
                    else:
                        cp['tokens_holding'] = order_results['tester_quantity']

                else:
                    cp['sell_price'] = order_price

                    if self.configuration['run_type'] == 'REAL':
                        cp['order_id']['S'] = order_results['orderId']

                cp['order_type'][order['side'][0]] = orderType
                cp['order_status'][order['side'][0]] = 'PLACED'

                logging.info('[BaseTrader][{0}] Update: {1}, type: {2}, status: {3}'.format(self.print_pair, updateOrder, orderType, cp['order_status']))
            return(cp)


    def _place_order(self, ptype, cp, order):
        ''' place order '''

        if self.rules['isFiat'] and self.rules['invFiatToBTC']:
            side = 'SELL' if order['side'] == 'BUY' else 'BUY'

        ## Calculate the quantity amount for the BUY/SELL side for long/short real/test trades.
        quantity = None
        if order['side'] == 'BUY':
            if self.rules['isFiat']:
                quantity = cp['currency_left']
            else:
                quantity = float(cp['currency_left'])/float(self.market_prices['bidPrice'])

            if self.configuration['run_type'] == 'REAL':
                if cp['order_id']['B']:
                    print("order id:", cp['order_id']['B'])
                    cancel_order_results = self._cancel_order(cp['order_id']['B'])
                    

        elif order['side'] == 'SELL':
            if self.rules['isFiat']:
                quantity = cp['tokens_holding']*float(self.market_prices['bidPrice'])
            else:
                if self.configuration['run_type'] == 'REAL':
                    tokens_holding = self.wallet_pair[self.base_asset][0]
                else:
                    tokens_holding = cp['tokens_holding']

            if ptype == 'LONG':
                quantity = float(tokens_holding)
            elif ptype == 'SHORT':
                if self.configuration['run_type'] == 'REAL':
                    for asset in self.rest_api.get_account(api_type='MARGIN')['userAssets']:
                        if asset['asset'] == self.base_asset:
                            quantity = float(asset['borrowed'])+float(asset['interest'])
                            break
                else:
                    quantity = float(tokens_holding)

            if self.configuration['run_type'] == 'REAL':
                if cp['order_id']['S']:
                    print("order id:", cp['order_id']['S'])
                    self._cancel_order(cp['order_id']['S'])

        ## Setup the quantity to be the correct precision.
        if quantity:
            split_quantity = str(quantity).split('.')
            quantity = float(split_quantity[0]+'.'+split_quantity[1][:self.rules['LOT_SIZE']])

        logging.info('quantity: {0}'.format(quantity))

        ## Place orders for both SELL/BUY sides for both TEST/REAL run types.
        if self.configuration['run_type'] == 'REAL':
            rData = {}
            ## Convert BUY to SELL if the order is a short (for short orders are inverted)
            if ptype == 'LONG':
                side = order['side']
            elif ptype == 'SHORT':
                if order['side'] == 'BUY':
                    ## Calculate the quantity required for a short loan.
                    loan_get_result = self.rest_api.apply_for_loan(asset=self.base_asset, amount=quantity)
                    rData.update({'loan_id':loan_get_result['tranId'], 'loan_cost':quantity})
                    side = 'SELL'
                else:
                    side = 'BUY'

            if order['ptype'] == 'MARKET':
                logging.info('[BaseTrader][{0}] side:{1}, type:{2}, quantity:{3}'.format(
                    self.print_pair, 
                    order['side'], 
                    order['ptype'], 
                    quantity))

                rData.update(self.rest_api.place_order(
                    self.configuration['market_type'], 
                    symbol=self.configuration['symbol'], 
                    side=side, 
                    type=order['ptype'], 
                    quantity=quantity))
                return({'action':'PLACED_MARKET_ORDER', 'data':rData})

            elif order['ptype'] == 'LIMIT':
                logging.info('[BaseTrader][{0}]  side:{1}, type:{2}, quantity:{3} price:{4}'.format(
                    self.print_pair, 
                    order['side'], 
                    order['ptype'], 
                    quantity,
                    order['price']))

                rData.update(self.rest_api.place_order(
                    self.configuration['market_type'], 
                    symbol=self.configuration['symbol'], 
                    side=side, 
                    type=order['ptype'], 
                    timeInForce='GTC', 
                    quantity=quantity,
                    price=order['price']))
                return({'action':'PLACED_LIMIT_ORDER', 'data':rData})

            elif order['ptype'] == 'STOP_LOSS_LIMIT':
                logging.info('[BaseTrader][{0}] side:{1}, type:{2}, quantity:{3} price:{4}, stopPrice:{5}'.format(
                    self.print_pair, 
                    order['side'], 
                    order['ptype'], 
                    quantity,
                    order['price'],
                    order['stopPrice']))

                rData.update(self.rest_api.place_order(
                    self.configuration['market_type'], 
                    symbol=self.configuration['symbol'], 
                    side=side, 
                    type=order['ptype'], 
                    timeInForce='GTC', 
                    quantity=quantity,
                    price=order['price'],
                    stopPrice=order['price']))
                return({'action':'PLACED_STOPLOSS_ORDER', 'data':rData})

        else:
            if order['ptype'] == 'MARKET':
                price = self.market_prices['lastPrice']
            else:
                price = order['price']

            return({'action':'PLACED_TEST_ORDER', 'data':{'type':'test', 'price':price, 'tester_quantity':float(quantity)}})


    def _cancel_order(self, order_id):
        ''' cancel orders '''
        if self.configuration['run_type'] == 'REAL':
            result = self.rest_api.cancel_order(self.configuration['market_type'], symbol=self.configuration['symbol'], orderId=order_id)
        else: result = 'CANCELED_TEST_ORDER'
        logging.debug('[BaseTrader][{0}] Cancel order results:\n{1}'.format(self.print_pair, str(result)))


    def _setup_market(self, side, cp):
        ''' Used to setup the trader for selling after a buy has been completed. '''

        if side == 'BUY':
            cp['currency_left']         = 0
            cp['order_id']['B']         = None
            cp['order_status']['B']     = None
            cp['order_type']['B']       = None
            cp['order_type']['S']       = 'WAIT'

        elif side == 'SELL':
            cp['sell_price']            = 0
            cp['buy_price']             = 0
            cp['currency_left']         = self.state_data['base_mac']
            cp['tokens_holding']        = 0
            cp['order_id']['S']         = None
            cp['order_description']['S']= None
            cp['order_status']['S']     = None
            cp['order_type']['S']       = None
            cp['order_description']['B']= None
            cp['order_type']['B']       = 'WAIT'

        return(cp)


    def get_indicator_data(self):
        return(self.indicators)


    def get_trader_data(self):
        trader_data = {
            'market':self.print_pair,
            'configuration':self.configuration,
            'market_prices':self.market_prices,
            'wallet_pair':self.wallet_pair,
            'custom_conditions':self.custom_conditional_data,
            'long_position':self.long_position,
            'trade_record':self.trade_recorder,
            'state_data':self.state_data,
            'rules':self.rules
        }

        if self.configuration['market_type'] == 'MARGIN':
            trader_data.update({'short_position':self.short_position})

        return(trader_data)


    def update_wallets(self, socket_buffer_global):
        last_wallet_update_time = socket_buffer_global['outboundAccountInfo']['E']
        foundBase = False
        foundQuote = False
        wallet_pair = {}

        for wallet in socket_buffer_global['outboundAccountInfo']['B']:
            if wallet['a'] == self.base_asset:
                wallet_pair.update({self.base_asset:[float(wallet['f']), float(wallet['l'])]})
                foundBase = True
            elif wallet['a'] == self.quote_asset:
                wallet_pair.update({self.quote_asset:[float(wallet['f']), float(wallet['l'])]})
                foundQuote = True

            if foundQuote and foundBase:
                break

        if not(foundBase):
            wallet_pair.update({self.base_asset:[0.0, 0.0]})
        if not(foundQuote):
            wallet_pair.update({self.quote_asset:[0.0, 0.0]})

        logging.info('[BaseTrader][{0}] New account data pulled, wallets updated.'.format(self.print_pair))
        return(wallet_pair, last_wallet_update_time)