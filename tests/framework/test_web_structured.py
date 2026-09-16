"""What JSON-LD buys us: a recipe nobody had to guess at.

Recipe sites publish schema.org markup for Google's benefit, and it is
strictly better than anything recoverable from the rendered page — the
ingredients are a list with quantities attached, the steps are ordered,
and no model was involved. This module pins that we read it, and that
we decline to read it when it is describing something other than the
page in front of us.

The recipe fixture is a live capture of essen-und-trinken.de, ld+json
untouched. Asserting against markup the site actually publishes is the
point: a hand-written fixture would agree with the parser by
construction and prove only that the two were written together.
"""

from __future__ import annotations

from stack.web.structured import read_structured


class TestRecipeIsReadWithoutAModel:
    """The measured case: a German recipe site, a plain fetch, a
    complete Recipe object."""

    def test_real_recipe_page_yields_ingredients_and_steps(self, web_fixture):
        content = read_structured(
            web_fixture("recipe-jsonld"),
            url="https://www.essen-und-trinken.de/rezepte/48816-rzpt-griechischer-salat",
        )

        assert content is not None
        assert content.mime == "text/markdown"
        assert "## Ingredients" in content.text
        assert "## Instructions" in content.text

    def test_the_title_comes_from_the_markup_not_the_slug(self, web_fixture):
        content = read_structured(web_fixture("recipe-jsonld"))

        assert content is not None
        assert "Griechischer Salat" in (content.title_hint or "")

    def test_every_published_ingredient_survives(self, web_fixture):
        """The site publishes 11 ingredients. Dropping one silently
        would be the worst kind of bug here — the entry still looks
        right, and the family only finds out at the stove."""
        content = read_structured(web_fixture("recipe-jsonld"))

        assert content is not None
        bullets = [ln for ln in content.text.splitlines() if ln.startswith("- ")]
        assert len(bullets) == 11

    def test_servings_and_total_time_are_rendered_readably(self, web_fixture):
        """`PT35M` is not something to show a family."""
        content = read_structured(web_fixture("recipe-jsonld"))

        assert content is not None
        assert "**Servings:** 4" in content.text
        assert "**Total time:** 35 min" in content.text


class TestListingPagesAreDeclined:
    """A round-up page embeds a Recipe object per thumbnail. Accepting
    one would file "our 30 best apple cakes" under the ingredients of
    whichever cake happened to be first in the markup."""

    def test_a_recipe_mention_without_ingredients_is_not_the_page(self):
        listing = """
        <html><script type="application/ld+json">
        {"@context":"https://schema.org","@type":"ItemList","itemListElement":[
          {"@type":"Recipe","name":"Apfelkuchen","image":"a.jpg"},
          {"@type":"Recipe","name":"Zwetschgenkuchen","image":"b.jpg"}
        ]}
        </script></html>
        """
        assert read_structured(listing) is None

    def test_a_recipe_with_ingredients_but_no_steps_is_not_the_page(self):
        partial = """
        <html><script type="application/ld+json">
        {"@type":"Recipe","name":"Teaser","recipeIngredient":["1 Apfel","Zucker"]}
        </script></html>
        """
        assert read_structured(partial) is None


class TestMarkupShapesInTheWild:
    """JSON-LD nests three ways and sites mix them. Each shape here is
    one seen in real markup, not a hypothetical."""

    def test_graph_wrapped_payloads_are_found(self):
        graphed = """
        <html><script type="application/ld+json">
        {"@context":"https://schema.org","@graph":[
          {"@type":"WebPage","name":"page"},
          {"@type":"Recipe","name":"Suppe",
           "recipeIngredient":["1 l Brühe"],
           "recipeInstructions":[{"@type":"HowToStep","text":"Erhitzen."}]}
        ]}
        </script></html>
        """
        content = read_structured(graphed)
        assert content is not None
        assert content.title_hint == "Suppe"
        assert "1. Erhitzen." in content.text

    def test_type_as_a_list_still_matches(self):
        """Sites commonly declare `"@type": ["Recipe", "NewsArticle"]`."""
        multi = """
        <html><script type="application/ld+json">
        {"@type":["Recipe","NewsArticle"],"name":"Brot",
         "recipeIngredient":["500 g Mehl"],
         "recipeInstructions":"Kneten.\\nBacken."}
        </script></html>
        """
        content = read_structured(multi)
        assert content is not None
        assert "1. Kneten." in content.text
        assert "2. Backen." in content.text

    def test_sections_are_flattened_into_one_numbered_list(self):
        """`HowToSection` groups steps ("for the dough", "for the
        topping"). The vault entry is prose, so the grouping is dropped
        and the order is kept."""
        sectioned = """
        <html><script type="application/ld+json">
        {"@type":"Recipe","name":"Tarte",
         "recipeIngredient":["Mehl"],
         "recipeInstructions":[
           {"@type":"HowToSection","name":"Teig","itemListElement":[
             {"@type":"HowToStep","text":"Mehl sieben."},
             {"@type":"HowToStep","text":"Butter zugeben."}]},
           {"@type":"HowToSection","name":"Belag","itemListElement":[
             {"@type":"HowToStep","text":"Äpfel schneiden."}]}]}
        </script></html>
        """
        content = read_structured(sectioned)
        assert content is not None
        assert "1. Mehl sieben." in content.text
        assert "3. Äpfel schneiden." in content.text

    def test_one_broken_block_does_not_cost_a_good_one(self):
        """Malformed ld+json is common. A trailing comma in the
        breadcrumb block must not hide the recipe in the next one."""
        mixed = """
        <html>
        <script type="application/ld+json">{"@type":"BreadcrumbList",}</script>
        <script type="application/ld+json">
        {"@type":"Recipe","name":"Salat","recipeIngredient":["Gurke"],
         "recipeInstructions":"Mischen."}
        </script></html>
        """
        content = read_structured(mixed)
        assert content is not None
        assert content.title_hint == "Salat"


class TestArticles:
    """`articleBody` is optional and usually absent, which is why
    general extraction still exists. When present it is the cleanest
    body available — the publisher's own idea of the article."""

    def test_article_body_is_used_when_published(self):
        article = """
        <html><script type="application/ld+json">
        {"@type":"NewsArticle","headline":"Local LLMs",
         "description":"A short note.",
         "articleBody":"Running models on your own hardware changes things."}
        </script></html>
        """
        content = read_structured(article)
        assert content is not None
        assert content.title_hint == "Local LLMs"
        assert "Running models on your own hardware" in content.text

    def test_an_article_stub_with_no_body_falls_through(self):
        """Almost every news page carries a headline-only NewsArticle
        object. Returning it would replace the real article with its
        own teaser."""
        stub = """
        <html><script type="application/ld+json">
        {"@type":"NewsArticle","headline":"Local LLMs","description":"A short note."}
        </script></html>
        """
        assert read_structured(stub) is None


class TestNoMarkup:
    def test_a_page_without_json_ld_falls_through(self):
        assert read_structured("<html><body><p>Just prose.</p></body></html>") is None

    def test_empty_input_is_handled(self):
        assert read_structured("") is None


class TestListingPagesAreFiledAsLists:
    """The other half of declining a listing: file the list itself.

    A chefkoch recipe search yielded 106 characters to extraction, which
    the gate called `empty`. True, and useless: the page carried forty
    recipes with names and links, published as structured data for
    exactly this purpose. A browser was measured on the same page and
    returned 34,000 characters of navigation and star ratings, which is
    worse than the list the site already hands us.
    """

    def test_a_real_listing_page_yields_its_entries(self, web_fixture):
        content = read_structured(web_fixture("listing-itemlist"))

        assert content is not None
        assert "40 entries on this page" in content.text
        assert "Rote Linsen mit Minzjoghurt" in content.text

    def test_every_entry_carries_a_link(self, web_fixture):
        """A list of names the family cannot open is a worse entry than
        no entry, so an item without a URL is dropped rather than
        listed."""
        content = read_structured(web_fixture("listing-itemlist"))

        assert content is not None
        bullets = [ln for ln in content.text.splitlines() if ln.startswith("- ")]
        assert len(bullets) == 40
        assert all("](http" in ln for ln in bullets)

    def test_the_page_title_becomes_the_entry_title(self, web_fixture):
        """An `ItemList` has no name of its own. Without a fallback the
        classifier is handed a titleless capture and invents one."""
        content = read_structured(web_fixture("listing-itemlist"))

        assert content is not None
        assert "Minzjoghurt" in (content.title_hint or "")


class TestAListingNeverOutranksTheRealSubject:
    """Ordering is the correctness argument for the whole dispatch.

    Recipe and article pages routinely carry a breadcrumb trail as
    structured data too. Walking objects in document order and taking
    the first that renders would file the breadcrumb and drop the
    recipe, so each reader gets the whole document before the next one
    is tried.
    """

    def test_a_recipe_page_is_still_read_as_a_recipe(self, web_fixture):
        content = read_structured(web_fixture("recipe-jsonld"))

        assert content is not None
        assert "## Ingredients" in content.text
        assert "entries on this page" not in content.text

    def test_a_recipe_wins_even_when_the_list_comes_first(self):
        """Document order deliberately puts the list ahead of the
        recipe, which is the arrangement that used to lose."""
        mixed = """
        <html>
        <script type="application/ld+json">
        {"@type":"ItemList","itemListElement":[
          {"@type":"ListItem","name":"Kuchen A","url":"https://e.com/a"},
          {"@type":"ListItem","name":"Kuchen B","url":"https://e.com/b"},
          {"@type":"ListItem","name":"Kuchen C","url":"https://e.com/c"},
          {"@type":"ListItem","name":"Kuchen D","url":"https://e.com/d"}]}
        </script>
        <script type="application/ld+json">
        {"@type":"Recipe","name":"Apfelkuchen","recipeIngredient":["2 Äpfel"],
         "recipeInstructions":"Backen."}
        </script></html>
        """
        content = read_structured(mixed)

        assert content is not None
        assert content.title_hint == "Apfelkuchen"
        assert "## Ingredients" in content.text

    def test_a_short_list_is_declined_as_a_breadcrumb(self):
        """Sites mislabel breadcrumb trails as `ItemList`, and a
        three-crumb trail is indistinguishable from a three-item list
        except by size."""
        crumbs = """
        <html><script type="application/ld+json">
        {"@type":"ItemList","itemListElement":[
          {"@type":"ListItem","name":"Home","url":"https://e.com/"},
          {"@type":"ListItem","name":"Rezepte","url":"https://e.com/r"},
          {"@type":"ListItem","name":"Salat","url":"https://e.com/r/s"}]}
        </script></html>
        """
        assert read_structured(crumbs) is None

    def test_a_list_of_bare_names_is_declined(self):
        assert read_structured("""
        <html><script type="application/ld+json">
        {"@type":"ItemList","itemListElement":[
          {"@type":"ListItem","name":"A"},{"@type":"ListItem","name":"B"},
          {"@type":"ListItem","name":"C"},{"@type":"ListItem","name":"D"}]}
        </script></html>
        """) is None
