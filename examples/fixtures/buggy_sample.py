"""Sample module with intentional bugs — used by the Waypoint code_reviewer demo."""


# Bug 1: Mutable default argument — shared state across calls
def append_item(item, lst=[]):
    lst.append(item)
    return lst


# Bug 2: Off-by-one — accesses items[i+1] without bounds check on last element
def find_consecutive_pair(items):
    for i in range(len(items)):
        if items[i] + 1 == items[i + 1]:
            return (items[i], items[i + 1])
    return None


# Bug 3: Unhandled division by zero — no guard when count is 0
def average(values):
    total = sum(values)
    count = len(values)
    return total / count


# Bug 4: Shadowed built-in — 'list' variable shadows the built-in list type
def process_data(raw):
    list = [1, 2, 3]           # shadows built-in
    result = list(raw)         # NameError at runtime: 'list' is now an int list
    return result


# Bug 5: Missing return value — returns None when caller expects an int
def count_evens(numbers):
    count = 0
    for n in numbers:
        if n % 2 == 0:
            count += 1
    # BUG: forgot `return count`


# Bug 6: Bare except — silently swallows all exceptions including KeyboardInterrupt
def load_config(path):
    try:
        with open(path) as f:
            import json
            return json.load(f)
    except:
        pass  # BUG: hides errors; caller never knows what went wrong


# --- Minimal harness so the file is importable without errors at module level ---

class DataPipeline:
    """Simple pipeline that chains the above functions."""

    def __init__(self, source_path):
        self.source_path = source_path
        self.data = []

    def load(self):
        config = load_config(self.source_path)   # silently fails
        if config:
            self.data = config.get("values", [])

    def run(self):
        self.load()
        pairs = find_consecutive_pair(self.data)
        avg = average(self.data)                 # crashes on empty list
        even_count = count_evens(self.data)      # always None
        return {
            "pairs": pairs,
            "avg": avg,
            "even_count": even_count,
        }
