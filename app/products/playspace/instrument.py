"""
Playspace instrument metadata constants.

The full instrument definition (sections, questions, scales) now lives in the
frontend as a static TypeScript constant. The backend retains only the metadata
needed for audit record creation and the stub version endpoint.
"""

INSTRUMENT_KEY = "pvua_v5_2"
INSTRUMENT_VERSION = "5.2"
INSTRUMENT_NAME = "Playspace Play Value and Usability Audit Tool"
