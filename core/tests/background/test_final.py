"""Terminal envelopes cannot be replaced by narration or partial addresses."""
import pytest
from raphael_core.background.final import validate_terminal

@pytest.mark.parametrize('text,reason',[(None,'missing_terminal'),('', 'missing_terminal'),('Done @','dangling_mention'),
    ('Done @ Bob','dangling_mention'),('Done @Unknown','unknown_mention')])
def test_incomplete_terminal_has_named_reason(text,reason):
    assert validate_terminal(text,names=['Bob']).reason==reason

@pytest.mark.parametrize('text',['Delivered @Bob','Contact a@example.com','Quote: `@`','正在 is a quoted fact, not a message type.','「@Unknown」'])
def test_valid_final_and_quoted_content_are_preserved(text):
    assert validate_terminal(text,names=['Bob']).text==text
    assert validate_terminal(text,names=['Bob']).reason is None

def test_chief_terminal_cannot_create_an_unserved_reply_intent():
    assert validate_terminal('Delivered @Bob',names=['Bob'],wrapup=True).reason=='dangling_mention'
