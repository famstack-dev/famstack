"""What the stack can learn about an AI server before using it.

`stack ai connect` asks two things beyond "does it answer": whether the
server transcribes, so voice messages do not fail later, and whether it
is on the home network, so nobody sends the family's documents to a
provider without being told.
"""

import pytest

from stack.ai.probe import stays_home, transcribes


class TestTranscription:

    def test_a_server_that_wants_the_missing_file_transcribes(self, httpserver):
        httpserver.expect_request("/v1/audio/transcriptions", method="POST") \
            .respond_with_data("file is required", status=422)

        assert transcribes(httpserver.url_for("/v1"))

    def test_a_server_without_the_route_does_not(self, httpserver):
        assert not transcribes(httpserver.url_for("/v1"))

    def test_nothing_listening_does_not(self):
        assert not transcribes("http://127.0.0.1:9/v1")


class TestHomeNetwork:

    @pytest.mark.parametrize("url", [
        "http://localhost:42060/v1",
        "http://127.0.0.1:8000/v1",
        "http://192.168.1.20:11434/v1",
        "http://10.0.0.5/v1",
        "http://100.101.102.103:8000/v1",   # a Tailscale address
    ])
    def test_addresses_in_the_house(self, url):
        assert stays_home(url)

    @pytest.mark.parametrize("url", [
        "https://8.8.8.8/v1",
        "https://1.1.1.1/v1",
    ])
    def test_addresses_outside_it(self, url):
        assert not stays_home(url)

    def test_a_name_that_does_not_resolve_is_not_assumed_home(self):
        assert not stays_home("https://nowhere.invalid/v1")
