"""當沖模組（與波段組合分開管理資金與風險）。

盤前  screener.build_watchlist：用前一日日線挑出流動性好、波動夠、有方向的股票，產生當日計畫
盤中  realtime.watch：輪詢證交所即時報價，價格觸發進場／停損／停利／強制平倉時推播
盤後  backtest：用 1 分 K 回測（demo 由日線合成分 K；真實資料可匯入 CSV）
策略  intraday.py：ORB（開盤區間突破）、VWAP（回測均價線後站回）
"""
