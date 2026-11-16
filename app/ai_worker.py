"""Optional trusted text cleanup; provider credentials never reach the PDF parser."""
import json
import os
from pathlib import Path
import sys
from .ai import cleanup_epub
from .budget import Budget

def main():
    book,db,job,cap,maximum,report=sys.argv[1:]
    budget=Budget(Path(db),total_ai_microusd=int(cap))
    result=cleanup_epub(Path(book),budget,job,os.environ.get('OPENAI_API_KEY',''),max_cost_microusd=int(maximum))
    Path(report).write_text(json.dumps(result))
if __name__=='__main__':main()
