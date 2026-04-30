from datasets import load_dataset

REPO_ID = "BridgewaterAIHackathon/BW-AI-Hackathon"
# Load a specific file from a folder inside the repo
dataset = load_dataset(
    REPO_ID,
    data_files="Structured_Data/SNE/yahoo-finance-data/stock_split_events.parquet",
    token=True,
)
# Confirm it worked
print(dataset['train'].to_pandas().head())