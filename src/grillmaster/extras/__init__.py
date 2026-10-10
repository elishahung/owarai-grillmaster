"""Optional agent extras beside the subtitles: the stylized cover, broadcast-date
research and Traditional Chinese title suggestions.

Each module takes explicit paths and values and runs its agent through an
`AgentRunner`; results are fixed-filename caches (an existing file is a hit).
Cover and date research back the side tasks in `stages/`; titles are produced
at package time.
"""
