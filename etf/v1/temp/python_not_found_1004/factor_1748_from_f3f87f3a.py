import pandas as pd

def calculate_1_day_SMA_of_Price_over_20_day_MIN_of_Price():
    df = pd.read_hdf("daily_pv.h5", key="data").sort_index()
    
    # Calculate 1-day SMA of Price (which is just the current close price)
    sma_1d = df["close"].groupby(level="instrument", sort=False).rolling(1).mean().droplevel(0)
    
    # Calculate 20-day MIN of Price
    min_20 = df["close"].groupby(level="instrument", sort=False).rolling(20).min().droplevel(0)
    
    # Formulation: SMA_1d(P) / MIN_{20}(P) - 1
    # Handle division by zero or NaNs appropriately for the final result
    numerator = sma_1d
    denominator = min_20
    
    # Perform the calculation
    values = (numerator / denominator) - 1
    
    # Ensure we keep the original index structure
    result = values.to_frame("1-day SMA of Price over 20-day MIN of Price").reindex(df.index)
    
    # Assert checks to ensure validity before saving
    assert result.index.nlevels == 2, "Index must have exactly 2 levels"
    non_null_count = int(result.notna().sum().sum())
    assert non_null_count > 0, "Factor must have at least one non-null value"
    
    # Save to HDF5
    result.to_hdf("result.h5", key="data")

if __name__ == "__main__":
    calculate_1_day_SMA_of_Price_over_20_day_MIN_of_Price()
