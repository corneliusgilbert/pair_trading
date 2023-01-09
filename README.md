# The implementation of cointegration-based pair trading strategy

## Portfolio construction
- Run Total Least Squares (TLS) Regression to find the beta (hedge ratio) of each pair
- Filtering pairs that is conintegrated based on ADF test for stationarity
- Rank each pair based on their half life of the mean reversion (the lower the better)
- Select the top 30 pairs and an equal-weight portfolio is constructed by the selected pairs

## Trading Signal
- The z-score for open position is +/- 1.5
- The z-score for close position is +/- 0.15
- 15% stop loss is applied to each pair

## Rebalancing
- Equal-weight portfolio is used in this strategy
- Pairs that are no longer cointegrated will be deleted from the portfolio

## Assumption:
- Transaction cost: commissions (8 bps), market impact (20 bps)
- Short-selling margin: exactly the same as the short size
