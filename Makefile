START    ?= 2025-01-01
END      ?= $(shell date -u +%Y-%m-%d)
LOOKBACK ?= 10

.PHONY: refresh refresh-btc backtest backtest-btc scan scan-btc clean

refresh:
	@./scripts/refresh.sh

refresh-btc:
	@./scripts/refresh.sh --btc

backtest:
	python3 -m smc_backtest.main --symbol EURUSD --start $(START) --end $(END)

backtest-btc:
	python3 -m smc_backtest.main --symbol BTCUSD --start $(START) --end $(END)

scan:
	python3 -m smc_backtest.main --symbol EURUSD --live --lookback $(LOOKBACK)

scan-btc:
	python3 -m smc_backtest.main --symbol BTCUSD --live --lookback $(LOOKBACK)

clean:
	rm -f smc_backtest_results*.png smc_trade_log.csv
