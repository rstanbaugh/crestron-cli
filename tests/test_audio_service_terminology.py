import io
import json
import unittest
from contextlib import redirect_stdout

from crestron_cli.main import _audio_command, _parse_query_selector


class AudioServiceTerminologyTests(unittest.TestCase):
    def test_query_selector_accepts_service_view(self) -> None:
        entity, room_selector, audio_view, parse_error = _parse_query_selector("audio", "service", None)
        self.assertEqual(entity, "audio")
        self.assertIsNone(room_selector)
        self.assertEqual(audio_view, "service")
        self.assertIsNone(parse_error)

    def test_query_selector_rejects_source_view(self) -> None:
        _, _, _, parse_error = _parse_query_selector("audio", "source", None)
        self.assertEqual(parse_error, "audio view 'source' was renamed to 'service'")

    def test_audio_action_service_requires_value(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = _audio_command(["Kitchen", "service", "--json"])
        self.assertEqual(code, 1)
        payload = json.loads(buffer.getvalue())
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "service action requires a service id or name")


if __name__ == "__main__":
    unittest.main()