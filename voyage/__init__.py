"""Weather-aware naval routing.

    ports      selectable world ports and the stops of Odysseus's voyage
    grid       0.5° ocean graph (land mask, 16 headings, canal and strait links)
    conditions forecast waves, wind and ocean currents along a route (Open-Meteo, cached)
    model      SuperLearner of a ship's speed over ground in given conditions (+ physics prior)
    storage    model artifacts on Vultr Object Storage (S3 API), local disk as fallback
    router     time-dependent A*: each step is costed with the conditions forecast for the
               hour the ship reaches the next point (time n+1), not the hour it leaves
    train      training job (python -m voyage.train), meant for a Vultr compute instance
"""
