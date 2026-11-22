# API routes

- `auth.py`: password signup, login, profile and logout.
- `google_oauth.py`: Google signup and login callbacks. `google_callback` checks the account; `create_login_response` handles the final cookie/redirect or token response.
- `monuments.py`: public monument search and detail cards.
- `agent_stories.py`: create or reuse a saved monument story.
- `tts.py`: turn that story into an MP3 with Fish Audio.
- `health.py`: check the API and MongoDB connection.

The routes check inputs and login, call the relevant helper, then return a response. Google login creates a new account only through the explicit signup flow.

The new `/auth/google/continue` flow signs in or creates a verified Google identity. `/auth/me` returns the public onboarding profile; `PUT /auth/profile` updates only the authenticated traveler. Legacy login/signup endpoints remain available.
