"""Small synthetic examples derived from the explicitly requested regression cases."""


def sample_audit():
    rows = [
        ("Heaven's River: Bobiverse, Book 4", "Dennis E. Taylor", "Ray Porter", "Bobiverse", "4", "Heaven's River"),
        ("The Gate of the Feral Gods: Dungeon Crawler Carl, Book 4", "Matt Dinniman", "Jeff Hays",
         "Dungeon Crawler Carl", "4", "The Gate of the Feral Gods"),
        ("17 -Battle Ground", "Jim Butcher", "James Marsters", "The Dresden Files", None, "Battle Ground"),
        ("02.5 Working for Bigfoot", "Jim Butcher", "James Marsters", "The Dresden Files", None, "Working for Bigfoot"),
        ("Harry Potter and the Goblet of Fire", "Stephen Fry", "J. K. Rowling", "Harry Potter", "4",
         "Harry Potter and the Goblet of Fire"),
        ("A Court of Mist and Fury", "Sarah J Maas", "Jennifer Ikeda", "A Court of Thorns and Roses", "2",
         "A Court of Mist and Fury"),
    ]
    return {"status": "complete", "books": [
        {"item_id": f"sample-{i}", "title": title, "subtitle": "", "authors": [author], "narrators": [narrator],
         "series": [{"name": series, "sequence": seq}],
         "rel_path": f"{author}/{series}/{seq or ('02.5' if i == 3 else '17')} - {clean} {{{narrator}}}",
         "embedded": {"title": clean}, "cover_present": i != 5,
         "chapter_count": 10, "chapter_sample": ["Chapter 1", "Chapter 2"]}
        for i, (title, author, narrator, series, seq, clean) in enumerate(rows)]}
