# Shared story operation

`stories.py` looks up a monument, returns its cached story if available, or generates and saves a new story. Both the story endpoint and a new tour session use this function so their behavior stays consistent.
