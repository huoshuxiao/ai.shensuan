import pandas as pd

def calculate_1_day_SMA_Price_over_60_day_MAX_Price():
    df = pd.read_hdf("daily_pv.h5", key="data").sort_index()
    
    # Calculate 1-day SMA of Price (which is just the current price)
    sma_1d = df["close"].groupby(level="instrument", sort=False).rolling(window=1).mean().droplevel(0)
    
    # Calculate 60-day MAX of Price
    max_60 = df["close"].groupby(level="instrument", sort=False).rolling(window=60).max().droplevel(0)
    
    # Calculate the ratio factor: SMA_1d(P) / MAX_{60}(P) - 1
    values = sma_1d / max_60 - 1
    
    result = values.to_frame("1-day SMA of Price over 60-day MAX of Price").reindex(df.index)
    
    # Self-check the shape before saving
    assert result.index.nlevels == 2 and int(result.notna().sum().sum()) > 0, "Factor calculation failed or produced all NaN"
    
    result.to_hdf("result.h5", key="data")

if __name__ == "__main__":
    calculate_1_day_SMA_Price_over_60_day_MAX_Price()
