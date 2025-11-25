Dashboard met streamlit. Started with Epexspot data. But followed up with Entso-e

## Setup

### API Key Configuration
This application requires an ENTSO-E API key. To configure it:

1. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`
2. Add your ENTSO-E API key to the secrets file:
   ```toml
   ENTSOE_API_KEY = "your-actual-api-key"
   ```

**For Streamlit Cloud deployment:**
Add the `ENTSOE_API_KEY` secret in your app settings on Streamlit Cloud.

You can obtain an API key from the [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/).
