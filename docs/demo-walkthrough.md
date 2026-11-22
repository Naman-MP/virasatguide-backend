# How the backend works

## Main request flow

1. FastAPI receives a request and checks its fields with Pydantic.
2. Protected endpoints check the user's token or login cookie.
3. A route loads the required data from MongoDB and calls a helper.
4. The helper uses the database, web search or an external AI/audio API.
5. FastAPI returns JSON, MP3 audio or a PDF to the frontend.

## Discover a monument

The list endpoint searches the monument name, city and location tag. It sorts by monument ID and returns one page of cards. Selecting a card loads the full record by ID or its city/name URL. Discovery does not call an AI model.

## Generate a story and audio

The backend first checks whether a story is already saved for that monument and model configuration. If not, a two-step LangGraph flow searches for context and asks the model for a narration using the database reference and search snippets. The backend cleans line breaks and escape characters, then saves the story.

The audio endpoint reads that saved text and sends it to Fish Audio. The MP3 is returned directly. Audio is not saved in MongoDB.

## Ask follow-up questions

A tour session saves a snapshot of the story, monument reference and research snippets. The backend splits these into passages. For each question it counts matching words, selects four passages and asks the model to answer using those passages. The previous question helps with follow-ups such as “Why was it built?”. Citation IDs must match the supplied passages.

MongoDB keeps only the latest three question-and-answer pairs for each session. A repeated request ID returns its saved answer while it remains in that history. A version check prevents two simultaneous writes from overwriting each other.

This is basic keyword retrieval, not a vector database or semantic search. A generated story or search snippet is not independently verified evidence, and model answers can still be wrong.

## Plan a trip and download it

The planner takes a destination and can also accept a budget, area, coordinates, interests, days and traveler count. It combines catalogue records with web search, then asks the model for itinerary cards. The backend checks days, stop order and citations. One correction attempt is allowed if those checks fail.

The backend calculates totals and whether the plan is over budget. Prices are estimates. Coordinates guide search but do not calculate distances or prove that a place is nearby. The PDF endpoint formats the saved plan without another model call.

## Which parts use AI?

Gemini or Ollama generates story text, question answers and itinerary suggestions. Python code handles login, database queries, retrieval, validation, history limits, cost calculations and PDF layout. Fish Audio provides speech. The frontend calls the backend; it does not receive provider keys.

## Demo readiness

Run the offline tests, then the real-service verification script. Use a short trip for local model generation. Prepare a signed-in account and a cached story before presenting. A healthy database check does not prove the model service is working.

Gemini is tried first once `GEMINI_API_KEY` is configured. Otherwise local development uses Ollama. Vercel needs a hosted Ollama URL for the fallback; it cannot connect to Ollama on this Mac through localhost. A live Gemini test still needs the real key.
