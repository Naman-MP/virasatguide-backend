# API routes

- `auth.py`: password signup, login, profile and logout.
- `google_oauth.py`: Google signup and login callbacks. `google_callback` checks the account; `create_login_response` handles the final cookie/redirect or token response.
- `monuments.py`: public monument search and detail cards.
- `agent_stories.py`: create or reuse a saved monument story.
- `tts.py`: turn that story into an MP3 with Fish Audio.
- `health.py`: check the API and MongoDB connection.

The routes check inputs and login, call the relevant helper, then return a response. Google login creates a new account only through the explicit signup flow.

The new `/auth/google/continue` flow signs in or creates a verified Google identity. `/auth/me` returns the public onboarding profile; `PUT /auth/profile` updates only the authenticated traveler. Legacy login/signup endpoints remain available.

## Endpoint flow

| Router | Requests and outcomes |
| --- | --- |
| [health.py](health.py) | Root returns a running message. Health and ping also check MongoDB and report 503 on failure. |
| [auth.py](auth.py) | Register validates and hashes a password. Login verifies the hash and issues a token. Me projects public fields. Profile updates personal details. Credentials attaches a first-time login to the authenticated Google account. Logout advances the token version and clears the cookie. |
| [google_oauth.py](google_oauth.py) | Status checks settings. Continue, login and signup start OAuth. Callback verifies the identity and matches a stable account, then starts a session and redirects. Legacy login asks new users to sign up. |
| [monuments.py](monuments.py) | Public listing filters by search and city and supports pagination. Details accept an ID or a city/monument slug pair. Missing records return 404. |
| [agent_stories.py](agent_stories.py) | Signed-in users request a cached or generated story from the shared service. |
| [tts.py](tts.py) | Find the saved story for the current model configuration, send its text to Fish Audio, then return validated MP3 bytes. Without a story, return 404. |

Send credentials on cross-origin cookie requests. The cookie write origin must be trusted. Bearer tokens follow the same account and expiry rules. [Travel routes](../travel/README.md) and the [complete API schema](../../../docs/openapi.json) cover the remaining endpoints.
