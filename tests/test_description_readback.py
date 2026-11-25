import pytest

from absmm.models import same_value


@pytest.mark.parametrize("plain,html", [
    ("William de Worde begins publishing...", "<p>William de Worde begins publishing...</p>"),
    ("First paragraph.\n\nSecond paragraph!", "<p>First paragraph.</p>\n<p>Second paragraph!</p>"),
    ('Édition & "news" — today.', '<p>Édition &amp; &quot;news&quot; &#8212; today.</p>'),
    ("2 < 3 & 5 > 4", "<p>2 &lt; 3 &amp; 5 &gt; 4</p>"),
    ("First.\r\n\r\nSecond.", "<p>First.</p><p>Second.</p>"),
])
def test_description_equivalence(plain, html):
    assert same_value("description", plain, html)
    assert same_value("description", html, plain)
    assert not same_value("title", plain, html)


@pytest.mark.parametrize("changed", [
    "<p>Changed words.</p><p>Second paragraph!</p>",
    "<p>First paragraph!</p><p>Second paragraph!</p>",
    "<p>First paragraph.</p>",
    "<p>First paragraph. Second paragraph!</p>",
    "<p>Second paragraph!</p><p>First paragraph.</p>",
    "<p>First paragraph.</p><p></p><p>Second paragraph!</p>",
    "<p>First paragraph.\n\nSecond paragraph!</p>",
    "<p><strong>First paragraph.</strong></p><p>Second paragraph!</p>",
    '<p style="color:red">First paragraph.</p><p>Second paragraph!</p>',
    "<p>First paragraph.<br>Second paragraph!</p>",
    "<p>First paragraph.</p><script>Second paragraph!</script>",
    "<p>First paragraph.</p><!-- hidden --><p>Second paragraph!</p>",
    "<p>First paragraph.</p><p>Second paragraph!",  # malformed
    "<p> First paragraph.</p><p>Second paragraph!</p>",
    None,
])
def test_material_differences_remain_blocked(changed):
    plain = "First paragraph.\n\nSecond paragraph!"
    assert not same_value("description", plain, changed)


def test_entities_decode_once_and_formatting_is_not_discarded():
    assert not same_value("description", "News & views", "<p>News &amp;amp; views</p>")
    assert not same_value("description", "News views", "<p>News&nbsp;views</p>")
    assert not same_value("description", "<em>News</em>", "News")
    assert not same_value("description", "<em>News</em>", "<p>&lt;em&gt;News&lt;/em&gt;</p>")
    assert same_value("description", "<em>News</em>", "<em>News</em>")
    assert not same_value("description", None, "<p></p>")
    assert not same_value("description", "News", "<p>News&#1;</p>")
    assert not same_value("description", "News �", "<p>News&#0;</p>")
    assert not same_value("description", "News & views", "<p>News &amp views</p>")
    assert not same_value("description", "¬it;", "<p>&notit;</p>")
    assert not same_value("description", "Roshar’s divided peoples", "<p>RosharΓÇÖs divided peoples</p>")
