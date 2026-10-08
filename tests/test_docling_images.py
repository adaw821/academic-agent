"""Exercise Docling's real API picture model and serializers without network."""
import os
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.base_models import ItemAndImageEnrichmentElement
from docling.models.stages.picture_description.picture_description_api_model import PictureDescriptionApiModel
from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size

from app.services.docling_images import picture_description_options, validate_picture_descriptions, PICTURE_PROMPT
from app.services.pdf_chunking import split_pdf_document
from app.services.ppt_chunking import clean_slide_text, split_slide_text


class BuiltinPictureTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"ZHIPU_API_KEY": "test-only"}))
        self.doc = DoclingDocument(name="builtin pictures")
        self.doc.add_page(page_no=1, size=Size(width=600, height=800))
        prov = ProvenanceItem(page_no=1, bbox=BoundingBox(l=0, t=0, r=100, b=100), charspan=(0, 0))
        self.doc.add_text(label=DocItemLabel.TEXT, text="Before picture", prov=prov)
        self.picture = self.doc.add_picture(prov=prov)
        self.doc.add_text(label=DocItemLabel.TEXT, text="After picture", prov=prov)
        self.model = PictureDescriptionApiModel(
            enabled=True, enable_remote_services=True, artifacts_path=None,
            options=picture_description_options(), accelerator_options=AcceleratorOptions(),
        )

    def describe(self, *, text="Visible evidence", ok=True):
        import json
        response = Mock(ok=ok, status_code=200 if ok else 503, headers={})
        response.text = json.dumps({
            "id": "test-completion", "created": 0,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.post.return_value = response
        with Image.new("RGB", (100, 100)) as image, patch(
            "docling.utils.api_image_request._make_retry_session", return_value=session,
        ):
            list(self.model(self.doc, [ItemAndImageEnrichmentElement(item=self.picture, image=image)]))
        return session.post.call_args

    def test_builtin_request_and_metadata_reach_pdf_and_ppt_children(self):
        text = "| key | value |\n| --- | --- |\n| x | 42 |"
        call = self.describe(text=text)
        payload = call.kwargs["json"]
        self.assertEqual(payload["model"], "glm-4.6v-flash")
        self.assertEqual(payload["messages"][0]["content"][1]["text"], PICTURE_PROMPT)
        self.assertEqual(call.kwargs["timeout"], 120)
        self.assertEqual(self.picture.meta.description.text, text)
        validate_picture_descriptions(self.doc)
        parents = split_pdf_document(self.doc)
        content = "\n".join(child.text for parent in parents for child in parent.children)
        self.assertIn(text, content)
        self.assertEqual(parents[0].page_numbers, [1])
        self.assertLess(content.index("Before picture"), content.index(text))
        self.assertLess(content.index(text), content.index("After picture"))
        slides = split_slide_text(clean_slide_text(self.doc.export_to_markdown(page_no=1)))
        self.assertEqual("\n".join(slides).count(text), 1)

    def test_builtin_http_error_cannot_be_treated_as_success(self):
        self.describe(ok=False)
        with self.assertRaisesRegex(ValueError, "图片描述为空"):
            validate_picture_descriptions(self.doc)

    def test_blank_model_output_is_rejected(self):
        self.describe(text=" ")
        with self.assertRaisesRegex(ValueError, "图片描述为空"):
            validate_picture_descriptions(self.doc)

    def test_no_pixels_or_description_does_not_invent_content(self):
        validate_picture_descriptions(self.doc)
        self.assertIsNone(self.picture.meta)


if __name__ == "__main__":
    unittest.main()
