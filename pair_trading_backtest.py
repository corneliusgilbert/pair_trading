import pyfolio as pf
import yfinance as yf
import pandas as pd
import math
from itertools import combinations
import numpy as np
import statsmodels.api as stat
import statsmodels.tsa.stattools as ts
import scipy.odr as odr
import statsmodels.api as sm
import backtrader as bt
import backtrader.feeds as btfeeds
import datetime as dt
import warnings
import os
warnings.simplefilter(action='ignore', category=FutureWarning)

Z_BENCHMARK = 1.5

'''
def johansen(price, combs):
    selected_evec=[]
    selected_combs=[]
    for comb in combs:
        try:
            jtest_result = coint_johansen(price.loc[:,comb], det_order=0, k_ar_diff=1)#we let k_ar_diff =1 for simplicity
            #print(jtest_result.lr2 > jtest_result.cvm[:,-2])
        except:
            continue
        if sum(jtest_result.lr2 > jtest_result.cvm[:,-2]) == len(combs[0]):
            selected_evec.append(jtest_result.evec[:,0])
            selected_combs.append(comb)
            spread = np.dot(price.loc[:,comb], jtest_result.evec[:,0])
            plt.plot(spread)
    return selected_evec, selected_combs

def hurst (spread):
    # Create the range of lag values
    lags = range(2, 100)
    # Calculate the array of the variances of the lagged differences
    tau = [np.var(np.subtract(spread[lag:], spread[:-lag])) for lag in lags]
    # Use a linear fit to estimate the Hurst Exponent
    #[tau==0] = 1
    poly = np.polyfit(np.log(lags), np.log(tau), 1)
    # Return the Hurst exponent from the polyfit output
    return poly[0]*2.0
'''

def linear_fitted_model (B,x):
    return B[0]+B[1]*x

def odr_estimate (x,y):
    linear = odr.Model(linear_fitted_model)
    data = odr.Data(x,y)
    odr_lm = odr.ODR(data, linear, beta0= [0., 0.])
    output = odr_lm.run()
    return output

def half_life (spread):
    lag = spread.shift(1)
    lag.iloc[0] =  lag.iloc[1]
    spread_ret = spread - lag
    spread_ret.iloc[0] = spread_ret.iloc[1]
    lag2 = sm.add_constant(lag)
    model = sm.OLS(spread_ret, lag2)
    res = model.fit()
    halflife = np.ceil(-np.log(2)/res.params.tolist()[1])
    return halflife

def coint_properties(log_close, combs):
  selected_pair1, selected_pair2, selected_pairs, selected_halflife, selected_hedge_ratio, selected_tstat, selected_pvalue, selected_z_latest, selected_z_max = [], [], [], [], [], [], [], [], []

  for i in range(len(combs)):
    if isinstance(combs, pd.DataFrame):
      pair = combs.loc[i]
      pair0 = pair[0]
      pair1 = pair[1]
    elif type(combs) == list:
      pair = combs[i]
      pair0 = pair[0]
      pair1 = pair[1]
    odr_estimate_output = odr_estimate(log_close[pair0], log_close[pair1])
    beta0, beta1 = odr_estimate_output.beta
    eps_hat = odr_estimate_output.eps
    adf = ts.adfuller(eps_hat, autolag='AIC')
    spread = log_close[pair[1]] - beta1 * log_close[pair[0]]
    z = (spread-np.mean(spread))/np.std(spread)
    z_score_latest = z.iloc[-1]
    z_score_max = np.max(np.abs(z))

    halflife = half_life(spread)
    selected_pair1.append(pair[0])
    selected_pair2.append(pair[1])
    selected_pairs.append(pair[0]+'_'+pair[1])
    selected_hedge_ratio.append(beta1)
    selected_tstat.append(adf[0])
    selected_pvalue.append(adf[1])
    selected_halflife.append(halflife)
    selected_z_latest.append(z_score_latest)
    selected_z_max.append(z_score_max)
  dct = {'pairs':selected_pairs, 'pair1':selected_pair1, 'pair2':selected_pair2, 'half_life':selected_halflife, 'n_pair1':selected_hedge_ratio, 'n_pair2':np.ones(len(selected_pairs)).tolist(), 'z_score_latest': selected_z_latest, 'z_score_max': selected_z_max, 't_stat':selected_tstat, 'pvalue':selected_pvalue}
  df_coint = pd.DataFrame(dct)
  df_coint = df_coint.sort_values(by="half_life", ascending=True)
  df_coint = df_coint.set_index([df_coint['pairs']])
  return df_coint

def pairs_constructor(df_coint):
  df_coint.drop(df_coint[(df_coint.pvalue >= 0.05) | (df_coint.n_pair1 < 0)].index, inplace = True)
  df_coint.drop(df_coint[np.abs(df_coint.z_score_max) < Z_BENCHMARK].index, inplace = True)
  df_coint = df_coint.sort_values(by="half_life", ascending=True)
  df_coint = df_coint.set_index('pairs')
  return df_coint

class PairTrading(bt.Strategy):
    params = {'lookback': 252, 'top_pairs': 30, 'abs_zscore': Z_BENCHMARK}
    
    def log(self, txt, dt=None):
        ''' Logging function for this strategy'''
        dt = dt or self.datas[0].datetime.date(0)
        print('%s, %s' % (dt.isoformat(), txt))
    
    def __init__(self):
        self.combs = combs2
        self.latest_pairs = None
        self.current_pairs = None
        self.portfolio = None
        self.cash = None
    
    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            # Buy/Sell order submitted/accepted to/by broker - Nothing to do
            return
    
    def next(self):
        
        if len(self) == self.p.lookback:
            current_data = pd.DataFrame()
            for t in iter(self.datas):
                current_data[t._name] = pd.Series(list(t.get(size=self.p.lookback)))
            log_current_data = np.log(current_data)
            
            coint_prop = coint_properties(log_current_data, self.combs).head(self.p.top_pairs)
            pairs_traded = pairs_constructor(coint_prop).head(self.p.top_pairs)
            self.latest_pairs = pairs_traded.index.tolist()
            
            #Now, start to allocate money for each pair (equal-weighted)
            if len(self.latest_pairs) != 0: #there are cointegrated pairs to trade
                cash_pair = self.broker.get_cash()/len(self.latest_pairs)
                nstock1_list, nstock2_list, price1_list, price2_list = [], [], [], []
                for pair in self.latest_pairs:
                    if pairs_traded.loc[pair,'z_score_latest'] <= -self.p.abs_zscore: #we long this pair
                        n_pair1 = pairs_traded.loc[pair, 'n_pair1'] #the hedge ratio
                        #long stock 2
                        stock2_tick = pairs_traded.loc[pair,'pair2']
                        price2 = current_data.tail(1)[stock2_tick].values[0]
                        n_stock2 = np.floor(1/(1+n_pair1)*cash_pair/price2)
                        self.buy(data = stock2_tick, size = n_stock2, exectype=bt.Order.Market)
                        self.log('BUY CREATE, %s, exectype Market, size %d, price %.2f' %(stock2_tick, n_stock2, price2))
                        
                        #Short stock 1
                        stock1_tick = pairs_traded.loc[pair,'pair1']
                        price1 = current_data.tail(1)[stock1_tick].values[0]
                        n_stock1 = np.floor(n_pair1/(1+n_pair1)*cash_pair/price1)
                        self.sell(data = stock1_tick, size = n_stock1, exectype=bt.Order.Market)
                        self.log('SELL CREATE, %s, exectype Market, size %d, price %.2f' %(stock1_tick, n_stock1, price1))
                        
                        n_stock1= -n_stock1 #to indicate short
                    
                    elif pairs_traded.loc[pair,'z_score_latest'] >= self.p.abs_zscore: #we short this pair
                        n_pair1 = pairs_traded.loc[pair, 'n_pair1'] #the hedge ratio
                        #Long stock 1
                        stock1_tick = pairs_traded.loc[pair,'pair1']
                        price1 = current_data.tail(1)[stock1_tick].values[0]
                        n_stock1 = np.floor(n_pair1/(1+n_pair1)*cash_pair/price1)
                        self.buy(data = stock1_tick, size = n_stock1, exectype=bt.Order.Market)
                        self.log('BUY CREATE, %s, exectype Market, size %d, price %.2f' %(stock1_tick, n_stock1, price1))
                        
                        #Short stock 2
                        stock2_tick = pairs_traded.loc[pair,'pair2']
                        price2 = current_data.tail(1)[stock2_tick].values[0]
                        n_stock2 = np.floor(1/(1+n_pair1)*cash_pair/price2)
                        self.sell(data = stock2_tick, size = n_stock2, exectype=bt.Order.Market)
                        self.log('SELL CREATE, %s, exectype Market, size %d, price %.2f' %(stock2_tick, n_stock2, price2))
                        
                        n_stock2 = -n_stock2 #to indicate short
                    
                    else:
                        n_stock1, n_stock2, price1, price2 = 0, 0, 0, 0
                        
                    nstock1_list.append(n_stock1)
                    nstock2_list.append(n_stock2)
                    price1_list.append(price1)
                    price2_list.append(price2)
                self.portfolio = pairs_traded.loc[self.latest_pairs, ['half_life','pair1','pair2']]
                self.portfolio['nstock1'] = nstock1_list
                self.portfolio['nstock2'] = nstock2_list
                self.portfolio['price1'] = price1_list
                self.portfolio['price2'] = price2_list
                self.portfolio['net_value'] = cash_pair
                
                self.current_pairs = [z.split('_') for z in self.latest_pairs]
            else:
                self.log('No pair found')
                
        elif len(self) > self.p.lookback:
            
            current_data = pd.DataFrame()
            for t in iter(self.datas):
                current_data[t._name] = pd.Series(list(t.get(size=self.p.lookback))) #using constant lookback period
            
            log_current_data = np.log(current_data)
            coint_prop = coint_properties(log_current_data, self.combs)
            coint_prop_copy = coint_prop.copy()
            pairs_traded = pairs_constructor(coint_prop_copy).head(self.p.top_pairs)
            self.latest_pairs = pairs_traded.index.tolist()
            
            #calculate pnl with reference to the buy/sell price of each asset
            pair1_current_price = (np.array(current_data.tail(1).loc[:,self.portfolio.pair1.tolist()])).flatten()
            pair2_current_price = (np.array(current_data.tail(1).loc[:,self.portfolio.pair2.tolist()])).flatten()
            
            self.portfolio['pnl'] = self.portfolio['nstock1']*(pair1_current_price-self.portfolio['price1']) + self.portfolio['nstock2']*(pair2_current_price-self.portfolio['price2'])
            self.portfolio['pct_pnl'] = self.portfolio['pnl']/self.portfolio['net_value']
            port_coint_prop = coint_properties(log_current_data, self.current_pairs)
            self.portfolio['z_score_latest'] = port_coint_prop.z_score_latest.tolist()
            self.portfolio['pvalue'] = port_coint_prop.pvalue.tolist()
            
            port_kept = self.portfolio.copy()
            deleted_port = []
            closed_port = []

            #We close everything we need to close with these criterias:
            
            #1. stop loss if loses > 15%
            #2. Z_score is trigerred
            #3. whether it is still cointegrated or not

            #Delete those that exceed stop loss limit
            deleted_port.extend(port_kept[port_kept.pct_pnl <= -0.15].index.tolist())
            closed_port.extend(port_kept[port_kept.pct_pnl <= -0.15].index.tolist())
            port_kept = port_kept[port_kept.pct_pnl > -0.15]
            
            #Delete those which no longer cointegrated
            deleted_port.extend(port_kept[port_kept.pvalue >= 0.05].index.tolist())
            closed_port.extend(port_kept[port_kept.pvalue >= 0.05].index.tolist())
            port_kept = port_kept[port_kept.pvalue < 0.05]
            
            #Close those which already profit
            cond1 = (port_kept['nstock1'] > 0) #case when we short the pair
            cond2 = (port_kept['nstock1'] <= 0) #case when we long the pair
            port_kept['z_signal'] = False
            port_kept.loc[cond1, 'z_signal'] = port_kept.loc[cond1, 'z_score_latest'] >= 0.15
            port_kept.loc[cond2, 'z_signal'] = port_kept.loc[cond2, 'z_score_latest'] <= -0.15
            closed_port.extend(port_kept[port_kept.z_signal == False].index.tolist())
            port_kept = port_kept[port_kept.z_signal]
            
            pairs_traded = pairs_traded.loc[~pairs_traded.index.isin(self.portfolio.index)]
            new_pairs_count = min(self.p.top_pairs - len(self.portfolio.index) +len(deleted_port), len(pairs_traded.index))
            total_pairs = len(self.portfolio.index) - len(deleted_port) + new_pairs_count

            #Close postions if one of those criterias trigerred and do the rebalancing
            nstock1_list, nstock2_list, price1_list, price2_list = [], [], [], []
            self.latest_pairs = []
            
            if len(self.portfolio.index) != total_pairs: #We added new pairs OR deleted existing pairs
                cash_pair= self.broker.getvalue()/total_pairs
            else:
                cash_pair = self.portfolio['net_value'][0]
            
            #Update pairs without open position
            pairs_without_open_pos = self.portfolio[self.portfolio['nstock1']==0]
            pairs_without_open_pos = pairs_without_open_pos[np.abs(pairs_without_open_pos['z_score_latest']) >= self.p.abs_zscore]
            pairs_without_open_pos = pairs_without_open_pos.loc[~pairs_without_open_pos.index.isin(deleted_port)]
            
            for pair in pairs_without_open_pos.index:
                if port_coint_prop.loc[pair,'z_score_latest'] <= -self.p.abs_zscore: #we long this pair
                    n_pair1 = port_coint_prop.loc[pair, 'n_pair1'] #the hedge ratio
                    #long stock 2
                    stock2_tick = port_coint_prop.loc[pair,'pair2']
                    price2 = current_data.tail(1)[stock2_tick].values[0]
                    n_stock2 = np.floor(1/(1+n_pair1)*cash_pair/price2)
                    self.buy(data = stock2_tick, size = n_stock2, exectype=bt.Order.Market)
                    self.log('BUY CREATE, %s, exectype Market, size %d, price %.2f' %(stock2_tick, n_stock2, price2))
                    
                    #Short stock 1
                    stock1_tick = port_coint_prop.loc[pair,'pair1']
                    price1 = current_data.tail(1)[stock1_tick].values[0]
                    n_stock1 = np.floor(n_pair1/(1+n_pair1)*cash_pair/price1)
                    self.sell(data = stock1_tick, size = n_stock1, exectype=bt.Order.Market)
                    self.log('SELL CREATE, %s, exectype Market, size %d, price %.2f' %(stock1_tick, n_stock1, price1))
                    
                    n_stock1= -n_stock1 #to indicate short
                
                elif port_coint_prop.loc[pair,'z_score_latest'] >= self.p.abs_zscore: #we short this pair
                    n_pair1 = port_coint_prop.loc[pair, 'n_pair1'] #the hedge ratio
                    #Long stock 1
                    stock1_tick = port_coint_prop.loc[pair,'pair1']
                    price1 = current_data.tail(1)[stock1_tick].values[0]
                    n_stock1 = np.floor(n_pair1/(1+n_pair1)*cash_pair/price1)
                    self.buy(data = stock1_tick, size = n_stock1, exectype=bt.Order.Market)
                    self.log('BUY CREATE, %s, exectype Market, size %d, price %.2f' %(stock1_tick, n_stock1, price1))
                    
                    #Short stock 2
                    stock2_tick = port_coint_prop.loc[pair,'pair2']
                    price2 = current_data.tail(1)[stock2_tick].values[0]
                    n_stock2 = np.floor(1/(1+n_pair1)*cash_pair/price2)
                    self.sell(data = stock2_tick, size = n_stock2, exectype=bt.Order.Market)
                    self.log('SELL CREATE, %s, exectype Market, size %d, price %.2f' %(stock2_tick, n_stock2, price2))
                    
                    n_stock2 = -n_stock2 #to indicate short
                
                else:
                    n_stock1, n_stock2, price1, price2 = 0, 0, 0, 0
                
                self.latest_pairs.append(pair)
                nstock1_list.append(n_stock1)
                nstock2_list.append(n_stock2)
                price1_list.append(price1)
                price2_list.append(price2)
            
            if len(closed_port) > 0:
                port_kept_list = port_kept.index.tolist()
                
                for pair in self.portfolio.index.tolist():
                    #We will exclude the case when n_stock1 is 0, meaning that we haven't had any open position in the pair
                    is_open_position = (self.portfolio.loc[pair,'nstock1'] != 0)
                    #Close out position
                    if (pair in closed_port) and is_open_position:
                        stock1_tick = self.portfolio.loc[pair,'pair1']
                        stock2_tick = self.portfolio.loc[pair,'pair2']
                        n_stock1 = self.portfolio.loc[pair,'nstock1']
                        n_stock2 = self.portfolio.loc[pair,'nstock2']
                        price1 = 0
                        price2 = 0
                        
                        if  n_stock1 < 0: #we short stock1 and long stock2, now we have to long stock 1 and short stock2 to close position
                            self.sell(data = stock2_tick, size = n_stock2, exectype=bt.Order.Market)
                            self.buy(data = stock1_tick, size = np.abs(n_stock1), exectype=bt.Order.Market)
                            self.log('Pair %s Closed' %(pair))
                
                        else: #we long stock1 and short stock2, now we have to short stock1 and long stock2 to close position
                            self.buy(data = stock2_tick, size = np.abs(n_stock2), exectype=bt.Order.Market)
                            self.sell(data = stock1_tick, size = n_stock1, exectype=bt.Order.Market)
                            self.log('Pair %s Closed' %(pair))

                        n_stock1 = 0
                        n_stock2 = 0
                    
                    #Rebalancing
                    elif (pair in port_kept_list) and is_open_position:
                        if self.portfolio.loc[pair,'z_score_latest'] <= -0.15: #we long this pair
                            n_pair1 = port_coint_prop.loc[pair, 'n_pair1'] #the hedge ratio
                            #long stock 2
                            stock2_tick = port_coint_prop.loc[pair,'pair2']
                            price2 = current_data.tail(1)[stock2_tick].values[0]
                            n_stock2 = np.floor(1/(1+n_pair1)*cash_pair/price2)
                            n_stock2_rebalance = n_stock2 - self.portfolio.loc[pair,'nstock2']              
                            
                            if n_stock2_rebalance > 0:
                                self.buy(data = stock2_tick, size = n_stock2_rebalance, exectype=bt.Order.Market)
                            else:
                                self.sell(data = stock2_tick, size = n_stock2_rebalance, exectype=bt.Order.Market)
                                
                            #Short stock 1
                            stock1_tick = port_coint_prop.loc[pair,'pair1']
                            price1 = current_data.tail(1)[stock1_tick].values[0]
                            n_stock1 = np.floor(n_pair1/(1+n_pair1)*cash_pair/price1)
                            n_stock1 = -n_stock1 #to indicate short
                            n_stock1_rebalance = n_stock1 - self.portfolio.loc[pair,'nstock1']
                            
                            if n_stock1_rebalance > 0:
                                self.buy(data = stock1_tick, size = n_stock1_rebalance, exectype=bt.Order.Market)
                            else:
                                self.sell(data = stock1_tick, size = n_stock1_rebalance, exectype=bt.Order.Market)

                        elif self.portfolio.loc[pair,'z_score_latest'] >= 0.15: #we short this pair
                            n_pair1 = port_coint_prop.loc[pair, 'n_pair1'] #the hedge ratio
                            #Long stock 1
                            stock1_tick = port_coint_prop.loc[pair,'pair1']
                            price1 = current_data.tail(1)[stock1_tick].values[0]
                            n_stock1 = np.floor(n_pair1/(1+n_pair1)*cash_pair/price1)
                            n_stock1_rebalance = n_stock1 - self.portfolio.loc[pair,'nstock1']
                            
                            if n_stock1_rebalance > 0:
                                self.buy(data = stock1_tick, size = n_stock1_rebalance, exectype=bt.Order.Market)
                            else:
                                self.sell(data = stock1_tick, size = n_stock1_rebalance, exectype=bt.Order.Market)
                            
                            #Short stock 2
                            stock2_tick = port_coint_prop.loc[pair,'pair2']
                            price2 = current_data.tail(1)[stock2_tick].values[0]
                            n_stock2 = np.floor(1/(1+n_pair1)*cash_pair/price2)
                            n_stock2 = -n_stock2 #to indicate short
                            n_stock2_rebalance = n_stock2 - self.portfolio.loc[pair,'nstock2']     
                            
                            if n_stock2_rebalance > 0:
                                self.buy(data = stock2_tick, size = n_stock2_rebalance, exectype=bt.Order.Market)
                            else:
                                self.sell(data = stock2_tick, size = n_stock2_rebalance, exectype=bt.Order.Market)
                        
                    if pair not in deleted_port and is_open_position:
                        self.latest_pairs.append(pair)
                        nstock1_list.append(n_stock1)
                        nstock2_list.append(n_stock2)
                        price1_list.append(price1)
                        price2_list.append(price2)
                        
                #If there is a deleted pair, we need to find replacement:
                if new_pairs_count > 0:
                    pairs_traded = pairs_traded.head(new_pairs_count)
                    for pair in pairs_traded.index:
                        if pairs_traded.loc[pair,'z_score_latest'] <= -self.p.abs_zscore: #we long this pair
                            n_pair1 = pairs_traded.loc[pair, 'n_pair1'] #the hedge ratio
                            #long stock 2
                            stock2_tick = pairs_traded.loc[pair,'pair2']
                            price2 = current_data.tail(1)[stock2_tick].values[0]
                            n_stock2 = np.floor(1/(1+n_pair1)*cash_pair/price2)
                            self.buy(data = stock2_tick, size = n_stock2, exectype=bt.Order.Market)
                            self.log('BUY CREATE, %s, exectype Market, size %d, price %.2f' %(stock2_tick, n_stock2, price2))
                            
                            #Short stock 1
                            stock1_tick = pairs_traded.loc[pair,'pair1']
                            price1 = current_data.tail(1)[stock1_tick].values[0]
                            n_stock1 = np.floor(n_pair1/(1+n_pair1)*cash_pair/price1)
                            self.sell(data = stock1_tick, size = n_stock1, exectype=bt.Order.Market)
                            self.log('SELL CREATE, %s, exectype Market, size %d, price %.2f' %(stock1_tick, n_stock1, price1))
                            
                            n_stock1= -n_stock1 #to indicate short
                        
                        elif pairs_traded.loc[pair,'z_score_latest'] >= self.p.abs_zscore: #we short this pair
                            n_pair1 = pairs_traded.loc[pair, 'n_pair1'] #the hedge ratio
                            #Long stock 1
                            stock1_tick = pairs_traded.loc[pair,'pair1']
                            price1 = current_data.tail(1)[stock1_tick].values[0]
                            n_stock1 = np.floor(n_pair1/(1+n_pair1)*cash_pair/price1)
                            self.buy(data = stock1_tick, size = n_stock1, exectype=bt.Order.Market)
                            self.log('BUY CREATE, %s, exectype Market, size %d, price %.2f' %(stock1_tick, n_stock1, price1))
                            
                            #Short stock 2
                            stock2_tick = pairs_traded.loc[pair,'pair2']
                            price2 = current_data.tail(1)[stock2_tick].values[0]
                            n_stock2 = np.floor(1/(1+n_pair1)*cash_pair/price2)
                            self.sell(data = stock2_tick, size = n_stock2, exectype=bt.Order.Market)
                            self.log('SELL CREATE, %s, exectype Market, size %d, price %.2f' %(stock2_tick, n_stock2, price2))
                            
                            n_stock2 = -n_stock2 #to indicate short
                        
                        else:
                            n_stock1, n_stock2, price1, price2 = 0, 0, 0, 0
                        
                        self.latest_pairs.append(pair)
                        nstock1_list.append(n_stock1)
                        nstock2_list.append(n_stock2)
                        price1_list.append(price1)
                        price2_list.append(price2)
            
                self.portfolio = coint_prop.loc[self.latest_pairs, ['half_life','pair1','pair2']]
                self.portfolio['nstock1'] = nstock1_list
                self.portfolio['nstock2'] = nstock2_list
                self.portfolio['price1'] = price1_list
                self.portfolio['price2'] = price2_list
                self.portfolio['net_value'] = cash_pair
                self.current_pairs = [z.split('_') for z in self.latest_pairs]
                

            else:
                for index in range(len(self.latest_pairs)):
                    self.portfolio.loc[self.latest_pairs[index], 'nstock1'] = nstock1_list[index]
                    self.portfolio.loc[self.latest_pairs[index], 'nstock2'] = nstock2_list[index]
                    self.portfolio.loc[self.latest_pairs[index], 'price1'] = price1_list[index]
                    self.portfolio.loc[self.latest_pairs[index], 'price2'] = price2_list[index]
                
            print(f"Portfolio value: {self.broker.getvalue()}")

if __name__ == "__main__":
    stock_list = pd.read_csv("/content/russell1000.csv")["Symbol"].tolist()
    stock_sectors = pd.read_csv("/content/russell1000.csv")["GICS Sector"].tolist()
    stock_data = yf.download(tickers=stock_list, start="2019-12-01", end="2022-11-30")


    # for Russell
    adjusted_close = stock_data.iloc[:, :1008]
    close = stock_data.iloc[:, 1008:2016]
    high = stock_data.iloc[:, 2016:3024]
    low = stock_data.iloc[:, 3024:4032]
    open = stock_data.iloc[:, 4032:5040]
    volume = stock_data.iloc[:, 5040:]

    close.columns = [x[1] for x in close.columns]
    volume.columns = [x[1] for x in volume.columns]

    to_discard = []
    close_count = close.count()
    volume_count = volume.count()
    for i in range(len(stock_list)):
        if close_count[i] != stock_data.shape[0] or volume_count[i] != stock_data.shape[0]:
            to_discard.append(volume.columns[i])

    updated_tickers = [stock for stock in stock_list if stock not in to_discard]
    volume = volume.drop(columns=to_discard)
    close = close.drop(columns=to_discard)

    pd.options.display.float_format = '{:.2f}'.format
    avg_volume = volume.mean()
    # updated_tickers2= avg_volume.loc[avg_volume > 10000000].index.tolist() # Russell
    updated_tickers2= avg_volume.loc[avg_volume > 5000000].index.tolist()

    # (close < 0).sum().sum() = 0 # no negative prices

    to_discard2 = []
    for ticker in updated_tickers:
        if ticker not in updated_tickers2:
            to_discard2.append(ticker)

    volume = volume.drop(columns=to_discard2)
    close = close.drop(columns=to_discard2)

    sectors = {}
    for ticker in updated_tickers2:
        val = ticker[:-3] # for india
        # val = ticker # for russell
        i = stock_list.index(val)
        sector = stock_sectors[i]
        if sector not in sectors.keys():
            sectors[sector] = []
        sectors[sector].append(ticker)

    combs2 = []

    # save all possible pairs
    for sector in sectors:
        for c in combinations(sectors[sector], 2):
            combs2.append(c)

        # convert to dataframe
    combs2 = np.array(combs2)
    combs2 = pd.DataFrame(combs2, columns=['s1','s2'])

    end = dt.datetime.now()
    start = end - dt.timedelta(days=504)

    stock_all ={}
    df_data = yf.download(tickers=updated_tickers2, start="2020-11-30", end="2022-11-30")
    ticker = updated_tickers2
    
    cerebro = bt.Cerebro()
    cerebro.addstrategy(PairTrading)

    for t in ticker:
        df_temp = df_data.loc[:,(slice(None),t)]
        df_temp.columns = [col[0] for col in df_temp.columns.values]
        stock_all[t] = df_temp
        data = bt.feeds.PandasData(dataname = stock_all[t])
        cerebro.adddata(data, name=t)    
    
    df_close = df_data.loc[:,'Close']
    df_close_log = np.log(df_close)
    
    # Set our desired cash start
    cerebro.broker.setcash(100000000.0)
    cerebro.broker.setcommission(commission=0.0028)  # 0.28% of commission (0.2% for market impact, 0.08% for trading commission)
    cerebro.broker.set_coc(True)

    # Print out the starting conditions
    print('Starting Portfolio Value: %.2f' % cerebro.broker.getvalue())

    # analyzers
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='mysharpe')
    cerebro.addanalyzer(bt.analyzers.PyFolio, _name='pyfolio')
    cerebro.addanalyzer(bt.analyzers.AnnualReturn, _name='annualret')
    cerebro.addanalyzer(bt.analyzers.Calmar, _name='calmar')
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='tradeanal')

    cerebro.addobserver(bt.observers.Value)

    # Run over everything
    strats = cerebro.run()
    strat = strats[0]

    pyfoliozer = strat.analyzers.getbyname('pyfolio')
    returns, positions, transactions, gross_lev = pyfoliozer.get_pf_items()

    # Print out the final result
    print('Final Portfolio Value: %.2f' % cerebro.broker.getvalue())
    print('Sharpe Ratio:', strat.analyzers.mysharpe.get_analysis())
    print("Annual Returns:", strat.analyzers.annualret.get_analysis())
    print("Calmar Ratio:", strat.analyzers.calmar.get_analysis())
    print("Max Drawdown: ", strat.analyzers.drawdown.get_analysis().max.drawdown)
    print("Trade Analysis:\n ", strat.analyzers.tradeanal.get_analysis())

    pf.create_full_tear_sheet(
        returns,
        positions,
        transactions,
        gross_lev,
        live_start_date=dt.datetime.strptime('2021/11/30', '%Y/%M/%d'),
        # sector_mappings=sectors,
        round_trips=True)