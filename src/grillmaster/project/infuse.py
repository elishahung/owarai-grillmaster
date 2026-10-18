"""The Infuse metadata sidecar of an archived project (`[features]
infuse_metadata`).

Infuse ignores the dates embedded in an MP4, and every archived video is
named `video.mp4`, so without a sidecar it cannot sort the archive by
broadcast date. Beside the video it reads an XML file of the same stem in its
own `<media type="Other">` schema (Firecore's home-video sample); a sidecar
replaces Infuse's metadata as a whole, so the description is written too.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grillmaster.project.state import ProjectState


# Characters XML 1.0 forbids even escaped; ElementTree writes them verbatim,
# and one would make Infuse drop the whole sidecar.
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def infuse_xml(state: ProjectState) -> str:
    """The sidecar XML: the platform's title and description and the
    broadcast date (`published`, left out when undated)."""
    media = ET.Element("media", type="Other")
    title = state.source.title or state.name or state.id
    ET.SubElement(media, "title").text = _XML_ILLEGAL.sub("", title)
    if state.source.description:
        description = _XML_ILLEGAL.sub("", state.source.description)
        ET.SubElement(media, "description").text = description
    aired = state.effective_broadcast_date
    if aired is not None:
        ET.SubElement(media, "published").text = aired.isoformat()
    ET.indent(media)
    return ET.tostring(media, encoding="unicode") + "\n"
