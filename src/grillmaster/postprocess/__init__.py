"""Agent post-processing of the translated subtitles: refine, then glossary check.

Both are file-writing agent passes. The agent works in its stage directory,
reads every other input by absolute path, and must keep the reference SRT
skeleton (block count, indexes, timecodes); a skeleton defect or a missing
mandatory file is a repair round in the same session, not a stage failure.
"""
