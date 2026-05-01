# Calendar-specific Hermes image.
#
# Based on the official Hermes Agent image, but explicitly adds runtime pieces
# required by this deployment:
#   - Matrix E2EE: libolm + mautrix[encryption]
#   - Local Russian STT: faster-whisper
#   - Local TTS: Piper neural TTS
#
# The base image still owns the entrypoint and /opt/data layout.
FROM nousresearch/hermes-agent:latest

USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      libolm-dev \
      espeak-ng \
      libespeak-ng1 \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install optional Python dependencies into Hermes' own virtualenv.
# Keep this explicit even if some packages are already present in the base
# image: the calendar image must fail at build time if E2EE/STT/TTS deps are
# unavailable, not silently fall back to plain Matrix or cloud voice.
RUN /opt/hermes/.venv/bin/python -m pip install --no-cache-dir \
      'mautrix[encryption]>=0.20,<1' \
      'faster-whisper>=1.0.0,<2' \
      'piper-tts>=1.3.0,<2'
