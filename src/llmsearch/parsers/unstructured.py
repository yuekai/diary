from enum import Enum, auto
from pathlib import Path
from typing import List, Union

from loguru import logger
from unstructured.documents.elements import NarrativeText, Text, Title
from unstructured.partition.html import partition_html
# from unstructured.partition.pdf import partition_pdf
from unstructured.partition.epub import partition_epub


class UnstructuredSplitType(Enum):
    PDF = auto()
    HTML = auto()
    EPUB = auto()


class UnstructuredSplitter:
    def __init__(self, document_type: UnstructuredSplitType) -> None:
        self.partition_function = {
 #            UnstructuredSplitType.PDF: partition_pdf,
            UnstructuredSplitType.HTML: partition_html,
            UnstructuredSplitType.EPUB: partition_epub,
        }[document_type]
        self.supported_elements = (NarrativeText, Text, Title)

    def split_document(
        self, document_path: Union[str, Path], max_size: int, **kwargs
    ) -> List[dict]:
        logger.info(f"Partitioning document: {document_path}")

        elements = self.partition_function(filename=str(document_path))

        logger.info("Combining document chunks...")

        all_chunks = []

        current_chunk = ""
        current_page = 1e8
        for el in elements:
            if not isinstance(el, self.supported_elements):
                continue
            text = "\n" + el.text

            # If element's text is larger than chunk size, split by characters
            if len(text) >= max_size - 1:
                # Flush the current chunk
                all_chunks.append(
                    {"text": current_chunk, "metadata": {"page": current_page}}
                )

                texts = text_split(text, max_size)
                logger.info(f"Sub-splitting large chunk. Got {len(texts)} sub-chunks.")
                for t in texts:
                    all_chunks.append(
                        {
                            "text": "\n" + t,
                            "metadata": {"page": el.metadata.page_number},
                        }
                    )

                current_chunk = ""
                current_page = 1e8

            # if element's text doesn't fit into current chunk - flush the current chunk and create a new chunk
            elif len(current_chunk + text) >= max_size:
                all_chunks.append(
                    {"text": current_chunk, "metadata": {"page": current_page}}
                )
                logger.info(
                    f"Flushing chunk. Length: {len(current_chunk)}, page: {el.metadata.page_number}"
                )
                current_chunk = text
                current_page = el.metadata.page_number

            # Otherwise, add element's text to current chunk, without re-assigning the page number
            else:
                current_chunk += text
                current_page = min(current_page, el.metadata.page_number)

        return all_chunks


def text_split(s, w):
    return [s[i : i + w] for i in range(0, len(s), w)]
