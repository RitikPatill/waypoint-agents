"""Standalone runner for the code_reviewer workflow."""
import asyncio
import os
import uuid

import anthropic
from dotenv import load_dotenv

from waypoint.workflow import WorkflowRunner
from waypoint.workflows import code_reviewer

load_dotenv()


async def main(file_path: str = "examples/fixtures/buggy_sample.py") -> None:
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    runner = WorkflowRunner(db_path="waypoint.db")
    run_id = str(uuid.uuid4())
    print(f"run_id: {run_id}")
    output = await runner.run(run_id=run_id, workflow=code_reviewer, prompt=file_path, client=client)
    print("\n=== Review summary ===")
    print(output)


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "examples/fixtures/buggy_sample.py"
    asyncio.run(main(path))
