from data_pipeline import convert_to_kes, get_fx_rates

print(get_fx_rates())         # Should print live rates including KES
print(convert_to_kes(100)) 