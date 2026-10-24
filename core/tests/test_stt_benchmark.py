from raphael_core.tools.stt_benchmark import benchmark


async def test_each_model_receives_identical_fixture_bytes_without_changing_settings():
    class Client:
        def __init__(self):
            self.calls = []

        async def transcribe(self, **kwargs):
            from types import SimpleNamespace
            self.calls.append(kwargs)
            return SimpleNamespace(cost_usd=.001)

    client = Client()
    rows = await benchmark(client, [("fixture.wav", b"same wav bytes")], ["chosen", "candidate"], 2)
    assert [call["model"] for call in client.calls] == ["chosen", "chosen", "candidate", "candidate"]
    assert len({call["audio_b64"] for call in client.calls}) == 1
    assert len(rows) == 2
    assert all(row["ok"] == 2 and row["cost_usd"] == .002 for row in rows)
