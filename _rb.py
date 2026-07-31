from engine import db, generator, publisher, seo
db.init()
rows = db.q("SELECT id,slug,title,role FROM pages")
for r in rows:
    db.x("UPDATE pages SET html=?, published=0 WHERE id=?",
         (generator.build_page(r["slug"], r["title"], r["role"]), r["id"]))
publisher.publish_pages()
a = seo.audit()
print("=== FINAL SEO AUDIT ===")
for k in ('pages','orphans','orphan_pct','avg_inbound','with_schema','with_faq','with_answer_first'):
    print(f'  {k:18s}: {a[k]}')
for h in a['health']: print('  *', h)
