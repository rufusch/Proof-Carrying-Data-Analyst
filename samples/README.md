# Synthetic integration datasheets

These files contain invented orders, not user/customer data. Generate them with
`python scripts/make_test_data.py`. `sales-duplicate.csv` is an exact byte copy
of `sales.csv`; the Excel workbook also contains a duplicate sheet. This tests
that the backend asks which table to use instead of silently doubling totals.

Known results are stored separately in `expected.json`: total amount 5,000;
units 50; profit 1,000; profit percentage 20%; amount/unit 100. West grows from
1,500 to 2,100 (40%); East falls from 800 to 600 (-25%). Periods are January and
February 2025, with inclusive starts and exclusive ends.
