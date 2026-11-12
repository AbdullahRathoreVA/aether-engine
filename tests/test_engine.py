"""Unit and integration tests. Standard library only, no network:

    python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORK = Path(tempfile.mkdtemp(prefix="aether-test-"))
for key in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "REDDIT_CLIENT_ID",
            "REDDIT_CLIENT_SECRET", "CHECKOUT_URL", "SITE_BASE_URL", "SUBREDDITS",
            "PRODUCT_NAME", "PRODUCT_PRICE", "SITE_AUTHOR", "SHOWCASE_URL",
            "SHOWCASE_ENABLED"):
    os.environ.pop(key, None)
os.environ["AETHER_HOME"] = str(WORK)
os.environ["OLLAMA_URL"] = "http://127.0.0.1:9"  # nothing listens here

PROFILE = json.loads((REPO / "examples" / "job-search-toolkit.json").read_text("utf-8"))
PROFILE["product"]["checkout_url"] = "https://shop.example/buy?plan=1"
PROFILE["site"]["base_url"] = "https://guides.example"
(WORK / "aether.json").write_text(json.dumps(PROFILE), encoding="utf-8")

sys.path.insert(0, str(REPO))
from engine import (brain, config, db, generator, keywords, llm, net, pages,  # noqa: E402
                    profile, publisher, render, scorer, seo, sources)

db.init()
brain.init()

BODY = ("<p>" + "Useful specific sentence about the topic at hand. " * 40 + "</p>"
        "<h2>First part</h2><ul><li>One concrete step.</li></ul>"
        "<h2>Second part</h2><p>" + "More detail that a reader can act on. " * 20 + "</p>")


def page(keyword: str, **over) -> dict:
    data = {"keyword": keyword, "title": f"{keyword.title()}: a practical guide",
            "description": f"A specific, usable guide to {keyword} that says exactly "
                           "what to do and in which order, with examples.",
            "answer": "Do the simplest thing that works first, then measure it "
                      "and change one variable at a time.",
            "body_html": BODY,
            "faqs": [{"q": "Does it work?", "a": "Yes, when applied consistently."},
                     {"q": "How long does it take?", "a": "About one afternoon."}]}
    data.update(over)
    return data


def tearDownModule():
    shutil.rmtree(WORK, ignore_errors=True)


class ProfileTests(unittest.TestCase):
    def check(self, mutate) -> dict:
        d = Path(tempfile.mkdtemp(dir=WORK))
        p = json.loads(json.dumps(PROFILE))
        mutate(p)
        (d / "aether.json").write_text(json.dumps(p), encoding="utf-8")
        return profile.validate(profile.load(d))

    def test_missing_profile_is_an_error(self):
        d = Path(tempfile.mkdtemp(dir=WORK))
        self.assertTrue(profile.validate(profile.load(d))["errors"])

    def test_starter_needs_a_product_name(self):
        d = Path(tempfile.mkdtemp(dir=WORK))
        profile.write_starter(d)
        self.assertIn("product.name is empty", profile.validate(profile.load(d))["errors"])
        with self.assertRaises(FileExistsError):
            profile.write_starter(d)

    def test_example_profile_is_valid(self):
        self.assertEqual(self.check(lambda p: None)["errors"], [])

    def test_broken_json_is_reported_not_raised(self):
        d = Path(tempfile.mkdtemp(dir=WORK))
        (d / "aether.json").write_text("{not json", encoding="utf-8")
        self.assertIn("not valid JSON", profile.validate(profile.load(d))["errors"][0])

    def test_rejects_script_urls_and_escaping_paths(self):
        bad = self.check(lambda p: p["product"].update(checkout_url="javascript:alert(1)"))
        self.assertTrue(any("checkout_url" in e for e in bad["errors"]))
        bad = self.check(lambda p: p["site"].update(dir="../outside"))
        self.assertTrue(any("site.dir" in e for e in bad["errors"]))
        bad = self.check(lambda p: p["sources"].update(subreddits=["jobs/../x"]))
        self.assertTrue(any("subreddit" in e for e in bad["errors"]))
        bad = self.check(lambda p: p["sources"].update(rss=["file:///etc/passwd"]))
        self.assertTrue(any("rss" in e for e in bad["errors"]))


class SanitiserTests(unittest.TestCase):
    def test_strips_scripts_handlers_and_script_links(self):
        out = render.sanitize(
            '<p onclick="x()">Hi <script>steal()</script><b>there</b></p>'
            '<a href="javascript:alert(1)">bad</a><iframe src="//evil"></iframe>'
            '<img src=x onerror=alert(1)><style>p{}</style>')
        for needle in ("script", "onclick", "javascript", "iframe", "onerror",
                       "steal", "img", "style"):
            self.assertNotIn(needle, out)
        self.assertIn("<p>Hi <b>there</b></p>", out)
        self.assertIn("bad", out)

    def test_keeps_safe_markup_and_marks_external_links(self):
        out = render.sanitize('<h2>T</h2><ul><li>a</li></ul>'
                              '<a href="https://example.com/x">ext</a>'
                              '<a href="other-page.html">int</a>')
        self.assertIn('<a href="https://example.com/x" rel="nofollow noopener">ext</a>', out)
        self.assertIn('<a href="other-page.html">int</a>', out)

    def test_closes_what_was_left_open_and_escapes_text(self):
        self.assertEqual(render.sanitize("<p>a < b <strong>c"), "<p>a &lt; b <strong>c</strong></p>")

    def test_href_cannot_break_out_of_its_attribute(self):
        self.assertEqual(render.safe_href('https://x.example/"><script>'), "")


class KeywordParsingTests(unittest.TestCase):
    def test_dataforseo_labs_shape(self):
        doc = {"tasks": [{"result": [{"items": [
            {"keyword": "ATS Resume Template",
             "keyword_info": {"search_volume": 5400, "cpc": 1.2, "competition": 0.3},
             "keyword_properties": {"keyword_difficulty": 22},
             "search_intent_info": {"main_intent": "commercial"}}]}]}]}
        (r,) = keywords.parse(json.dumps(doc))
        self.assertEqual(r, {"keyword": "ats resume template", "volume": 5400,
                             "difficulty": 22.0, "cpc": 1.2, "competition": 0.3,
                             "intent": "commercial"})

    def test_related_keywords_and_google_ads_shapes(self):
        related = {"tasks": [{"result": [{"items": [{"keyword_data": {
            "keyword": "resume keywords", "keyword_info": {"search_volume": 900}}}]}]}]}
        self.assertEqual(keywords.parse(json.dumps(related))[0]["volume"], 900)
        ads = {"tasks": [{"result": [{"keyword": "cover letter", "search_volume": 100,
                                      "competition_index": 40, "cpc": 0.5}]}]}
        r = keywords.parse(json.dumps(ads))[0]
        self.assertEqual((r["volume"], r["competition"]), (100, 0.4))

    def test_csv_exports(self):
        semrush = "Keyword,Volume,KD %,CPC (USD),Intent\nresume help,\"1,300\",35,0.80,Informational\n"
        r = keywords.parse(semrush)[0]
        self.assertEqual((r["volume"], r["difficulty"], r["intent"]), (1300, 35.0, "informational"))
        semi = "﻿Query;Search Volume\nats checker;2.4K\n"
        self.assertEqual(keywords.parse(semi)[0]["volume"], 2400)

    def test_plain_lines_and_rubbish(self):
        self.assertEqual([r["keyword"] for r in keywords.parse("one keyword\n\nTwo  Keyword\n")],
                         ["one keyword", "two keyword"])
        self.assertEqual(keywords.parse(""), [])
        self.assertEqual(keywords.parse("<script>x</script>"), [])
        self.assertEqual(keywords.parse('{"nothing": [1, 2]}'), [])

    def test_out_of_range_numbers_are_dropped_not_stored(self):
        r = keywords.parse('[{"keyword": "x y", "volume": -5, "difficulty": 400}]')[0]
        self.assertEqual((r["volume"], r["difficulty"]), (None, None))


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.x("DELETE FROM keywords")
        keywords.store(keywords.parse(json.dumps([
            {"keyword": "ats resume template", "volume": 5400, "difficulty": 20},
            {"keyword": "ats resume templates", "volume": 900, "difficulty": 25},
            {"keyword": "template for ats resume", "volume": 300},
            {"keyword": "cover letter help", "volume": 2000, "difficulty": 30},
            {"keyword": "sourdough starter recipe", "volume": 90000, "difficulty": 10},
            {"keyword": "linkedin profile help"}])), "test")

    def test_near_duplicates_become_one_topic(self):
        topics = keywords.plan(10)["topics"]
        ats = [t for t in topics if "ats" in t["keyword"]]
        self.assertEqual(len(ats), 1)
        self.assertEqual(ats[0]["keyword"], "ats resume template")
        self.assertCountEqual(ats[0]["also_target"],
                              ["ats resume templates", "template for ats resume"])
        self.assertEqual(ats[0]["volume"], 6600)

    def test_relevance_beats_raw_volume(self):
        order = [t["keyword"] for t in keywords.plan(10)["topics"]]
        self.assertLess(order.index("ats resume template"),
                        order.index("sourdough starter recipe"))

    def test_unknown_numbers_are_declared(self):
        out = keywords.plan(10)
        self.assertIn("no search-volume data", out["note"])
        li = next(t for t in out["topics"] if t["keyword"] == "linkedin profile help")
        self.assertFalse(li["why"]["volume_known"])

    def test_store_never_replaces_a_known_number_with_unknown(self):
        keywords.store([{"keyword": "cover letter help", "volume": None, "difficulty": None,
                         "cpc": None, "competition": None, "intent": ""}], "later")
        row = db.one("SELECT volume FROM keywords WHERE keyword='cover letter help'")
        self.assertEqual(row["volume"], 2000)

    def test_brief_carries_the_cluster_and_a_fillable_page(self):
        b = keywords.brief("ats resume template")
        self.assertEqual({k["keyword"] for k in b["also_target"]},
                         {"ats resume templates", "template for ats resume"})
        self.assertEqual(b["page_json"]["slug"], "ats-resume-template")
        self.assertEqual(b["product"]["name"], "AI Job-Search Toolkit")
        with self.assertRaises(ValueError):
            keywords.brief("   ")


class PageAndPublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.x("DELETE FROM pages")
        shutil.rmtree(config.SITE_DIR, ignore_errors=True)
        for kw in ("alpha topic", "beta topic", "gamma topic", "delta topic"):
            pages.add(page(kw))
        publisher.publish_pages()

    def test_quality_gate(self):
        for bad, needle in ((page("thin one", body_html="<p>too short</p>"), "words"),
                            (page("bad slug", slug="../../etc/passwd"), "slug"),
                            (page("index", slug="index"), "slug"),
                            (page("no answer", answer=""), "answer"),
                            (page("alpha topic"), "exists")):
            with self.assertRaises(ValueError) as ctx:
                pages.add(bad)
            self.assertIn(needle, str(ctx.exception))
        self.assertFalse((WORK.parent / "etc").exists())

    def test_body_is_sanitised_before_storage(self):
        pages.add(page("unsafe body", body_html=BODY + "<script>alert(1)</script>"))
        stored = db.one("SELECT body FROM pages WHERE slug='unsafe-body'")["body"]
        self.assertNotIn("script", stored)
        pages.remove("unsafe-body")

    def test_every_page_is_complete_and_linked(self):
        report = seo.audit()
        self.assertEqual((report["pages"], report["errors"], report["orphans"]), (4, 0, 0))
        self.assertEqual(report["with_schema"], 4)
        self.assertEqual(report["with_faq"], 4)
        self.assertEqual(report["with_answer_first"], 4)
        self.assertEqual(report["with_buy_link"], 4)
        self.assertEqual(report["sitemap"], "lists every page")

    def test_a_new_page_is_linked_from_older_pages(self):
        """The orphan regression: old pages must gain a link to a new one."""
        pages.add(page("zeta topic"))
        publisher.publish_pages()
        linked_from = [p.name for p in config.SITE_DIR.glob("*-topic.html")
                       if 'href="zeta-topic.html"' in p.read_text("utf-8")
                       and p.name != "zeta-topic.html"]
        self.assertTrue(linked_from)
        self.assertEqual(seo.audit()["orphans"], 0)
        pages.remove("zeta-topic")
        publisher.publish_pages()
        self.assertFalse((config.SITE_DIR / "zeta-topic.html").exists())
        self.assertEqual(seo.audit()["errors"], 0)

    def test_publish_is_idempotent(self):
        self.assertEqual(publisher.publish_pages(), 0)

    def test_structured_data_parses_and_cannot_break_out(self):
        pages.add(page("tricky faq", faqs=[
            {"q": "Is </script><script>alert(1)</script> safe?", "a": "It is \"quoted\" & escaped."},
            {"q": "Second?", "a": "Yes."}]))
        publisher.publish_pages()
        text = (config.SITE_DIR / "tricky-faq.html").read_text("utf-8")
        self.assertEqual(text.count("<script"), 1)
        block = text.split('<script type="application/ld+json">')[1].split("</script>")[0]
        types = [n["@type"] for n in json.loads(block)["@graph"]]
        self.assertEqual(types, ["Article", "FAQPage", "BreadcrumbList"])
        pages.remove("tricky-faq")
        publisher.publish_pages()

    def test_buy_link_names_the_page_that_sent_the_visitor(self):
        text = (config.SITE_DIR / "alpha-topic.html").read_text("utf-8")
        self.assertIn("https://shop.example/buy?plan=1&amp;utm_source=aether"
                      "&amp;utm_medium=seo&amp;utm_campaign=alpha-topic", text)
        self.assertIn("<link rel=\"canonical\" href=\"https://guides.example/alpha-topic.html\">", text)

    def test_audit_reports_a_missing_file_and_a_broken_link(self):
        victim = config.SITE_DIR / "beta-topic.html"
        saved = victim.read_text("utf-8")
        victim.unlink()
        report = seo.audit()
        self.assertEqual(report["errors"], 1)
        self.assertIn("file is missing", report["issues"][0]["issue"])
        victim.write_text(saved, encoding="utf-8", newline="\n")

        pages.add(page("dangling link",
                       body_html=BODY + '<p><a href="nowhere-page.html">x</a></p>'))
        publisher.publish_pages()
        issues = [i["issue"] for i in seo.audit()["issues"] if i["severity"] == "error"]
        self.assertEqual(issues, ["broken internal link to nowhere-page.html"])
        pages.remove("dangling-link")
        publisher.publish_pages()
        self.assertEqual(seo.audit()["errors"], 0)

    def test_matrix_builder_uses_the_profile_templates(self):
        report = generator.generate_seo_report(2)
        self.assertEqual(report["pages_built"], 2)
        row = db.one("SELECT * FROM pages WHERE cluster!='' AND role!='' LIMIT 1")
        self.assertIn(row["role"], config.SEO_ROLES)
        self.assertNotIn("{subject}", row["answer"] + row["body"] + row["title"])
        db.x("DELETE FROM pages WHERE role!=''")
        publisher.publish_pages()

    def test_version_1_rows_are_adopted_and_relinked(self):
        old = render.PAGE_SHELL.format(
            title="Old page", h1="Old page", desc="An older page kept from version one.",
            canonical="old-page.html", schema="{}", date="1 July 2026", readtime=2,
            content='<div class="answer"><strong>Short answer.</strong> More.</div>'
                    + BODY + '<section class="faq"><h2>Common questions</h2>'
                    '<h3>Q one?</h3><p>A one.</p></section>'
                    '<nav class="related" aria-label="Related guides"><h2>Related guides</h2>'
                    '<ul><li><a href="gone.html">Gone</a></li></ul></nav>',
            cta='<div class="cta"><h3>x</h3></div>', showcase="", home="index.html",
            product="P")
        db.x("INSERT INTO pages(slug,title,role,keyword,html,created_at,published) "
             "VALUES('old-page','Old page','nurse','old page',?,?,1)", (old, 1.75e9))
        publisher.publish_pages()
        row = db.one("SELECT * FROM pages WHERE slug='old-page'")
        self.assertEqual(row["answer"], "Short answer. More.")
        self.assertEqual(json.loads(row["faqs"]), [{"q": "Q one?", "a": "A one."}])
        text = (config.SITE_DIR / "old-page.html").read_text("utf-8")
        self.assertNotIn("gone.html", text)
        self.assertIn("alpha-topic.html", text)
        pages.remove("old-page")
        publisher.publish_pages()


class HarvestTests(unittest.TestCase):
    FIXTURES = {
        "hn.algolia.com": json.dumps({"hits": [
            {"objectID": "1", "title": "Ask HN: my resume gets no callback?",
             "story_text": "I applied to 100 jobs and my resume is not getting replies. " * 3,
             "author": "a", "created_at_i": 1750000000},
            {"objectID": "2", "comment_text": "short", "story_title": "x"}]}),
        "api.stackexchange.com": json.dumps({"items": [
            {"question_id": 7, "link": "https://workplace.stackexchange.com/q/7",
             "title": "How do I get past ATS filters?", "body": "<p>Body text</p>",
             "owner": {"display_name": "b"}, "creation_date": 1750000000}]}),
        "hnrss.org": ('<rss><channel><item><title>Resume &amp; ATS advice</title>'
                      '<link>https://example.org/post</link>'
                      '<description>&lt;p&gt;Hello&lt;/p&gt;</description></item>'
                      '<item><title>No link here</title></item>'
                      '<item><title>Bad scheme</title><link>javascript:alert(1)</link></item>'
                      '</channel></rss>'),
    }

    def setUp(self):
        self._fetch = net.fetch
        db.x("DELETE FROM signals")

        def fake(url, **kw):
            for host, payload in self.FIXTURES.items():
                if host in url:
                    return payload
            raise OSError("network is disabled in tests")
        net.fetch = fake

    def tearDown(self):
        net.fetch = self._fetch

    def test_each_source_is_parsed_and_failures_are_named(self):
        report = sources.harvest_report()
        self.assertEqual(report["by_source"],
                         {"reddit": 0, "hackernews": 1, "stackexchange": 1, "rss": 1})
        self.assertEqual(len(report["unreachable"]), len(config.SUBREDDITS))
        rss = db.one("SELECT * FROM signals WHERE source='rss'")
        self.assertEqual((rss["url"], rss["title"], rss["body"]),
                         ("https://example.org/post", "Resume & ATS advice", "Hello"))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM signals WHERE url LIKE 'javascript%'"), 0)

    def test_second_sweep_adds_nothing(self):
        sources.harvest_report(["hackernews"])
        self.assertEqual(sources.harvest_report(["hackernews"])["new_signals"], 0)

    def test_scoring_and_questions(self):
        sources.harvest_report(["hackernews", "stackexchange"])
        self.assertEqual(scorer.keyword_score("We are hiring", "resume not getting " * 20), 0.0)
        self.assertGreaterEqual(
            scorer.keyword_score("x", "my resume not getting replies, no callback? " * 5), 6.0)
        scorer.score_pending(50)
        top = scorer.top_signals(5)
        self.assertEqual(top[0]["source"], "hackernews")
        qs = [q["question"] for q in keywords.questions(10)]
        self.assertIn("my resume gets no callback?", qs)
        self.assertNotIn("How we used to get jobs: A newspaper classifieds story", qs)

    def test_only_http_is_ever_fetched(self):
        net.fetch = self._fetch
        with self.assertRaises(ValueError):
            net.fetch("file:///etc/passwd")


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        db.x("DELETE FROM content")
        db.x("DELETE FROM training")
        db.x("DELETE FROM signals")
        self.sid = db.x(
            "INSERT INTO signals(source,external_id,url,title,body,harvested_at,status,score)"
            " VALUES('hackernews','t1','https://example.org/1','Need resume help','body',1,'scored',5)")

    def test_a_decision_is_recorded_exactly_once(self):
        did = generator.add_draft(self.sid, "A genuinely helpful reply. " * 4)
        self.assertEqual(len(publisher.queue()), 1)
        self.assertFalse(publisher.mark_posted(did))          # not approved yet
        self.assertTrue(publisher.approve(did))
        self.assertFalse(publisher.approve(did))              # double click
        self.assertFalse(publisher.reject(did))
        self.assertFalse(publisher.reject(99999))             # bad id
        self.assertEqual(len(brain.samples("reply")), 1)
        self.assertTrue(publisher.mark_posted(did, "https://example.org/r"))
        self.assertEqual(publisher.queue(), [])

    def test_drafts_need_a_real_signal_and_real_text(self):
        with self.assertRaises(ValueError):
            generator.add_draft(424242, "long enough text " * 5)
        with self.assertRaises(ValueError):
            generator.add_draft(self.sid, "hi")

    def test_model_refuses_to_predict_until_it_has_evidence(self):
        score, why = brain.score("resume help", 3.0)
        self.assertEqual(score, 3.0)
        self.assertIn("keyword rules only", why)
        self.assertIsNone(brain.accuracy("reply"))

    def test_nothing_can_post_to_a_platform(self):
        self.assertIs(config.AUTO_PUBLISH_SOCIAL, False)
        for mod in (publisher, generator, sources):
            self.assertFalse([n for n in dir(mod) if "submit" in n or n.startswith("post_")])


class LlmTests(unittest.TestCase):
    def test_no_keys_means_no_model_and_no_filler(self):
        self.assertEqual(llm.detect_backend(force=True), "template")
        self.assertEqual(llm.generate("anything"), "")

    def test_a_failing_backend_is_benched(self):
        llm._benched.clear()
        llm._fails.clear()
        old, cache = config.GROQ_API_KEY, llm._backend_cache
        config.GROQ_API_KEY = "test-key"
        real = llm._call
        calls = []

        def boom(backend, *a):
            if backend != "groq":
                return ""
            calls.append(1)
            raise OSError("down")
        llm._call = boom
        try:
            for _ in range(6):
                llm._backend_checked = 0
                llm.generate("x")
            self.assertEqual(len(calls), llm._BENCH_AFTER)
            self.assertEqual(llm.detect_backend(force=True), "template")
        finally:
            llm._call, config.GROQ_API_KEY, llm._backend_cache = real, old, cache
            llm._benched.clear()
            llm.detect_backend(force=True)


if __name__ == "__main__":
    unittest.main()
