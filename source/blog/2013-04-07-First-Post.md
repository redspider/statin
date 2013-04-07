<abbr class="published" title="2005-10-24T09:49:00">9:49 pm</abbr>

Blog posts are easy enough to create, indeed for most static generators the problem is less the creation and rendering
of blog posts as it is obtaining the metadata from the posts in order to create good aggregate views.

The typical problem is that the content of the blog post is not easily accessible if it's done as a Markdown file such
as this, whereas if the Markdown is embedded within some kind of metadata file format such as YML it's just a pain to
write and not really compatible with regular Markdown editors.

We solve this problem in two ways. Firstly we acknowledge the requirement to be able to have Markdown in its own file,
which means we have to have a way of extracting the text. Fortunately there's a straightforward way of doing that:

```
{{ grab('my-blog-post.md').as_html() }}
```

We can even obtain subsets of it using selectors:

```
{{ select(grab('my-blog-post.md').as_html(),'h1').text() }}
```

This example grabs the content of the h1 tag (1st-level heading) within a simple HTML transform of a Markdown file.

Specifically, this solves the problem of being able to include the title or the first couple of paragraphs from the
blog post within an aggregate page somewhere else.

The second issue relates to metadata, particularly around the date it was published or similar. The simplest way to
deal with this is to include a microformat in your Markdown:

```
<abbr class="published" title="2005-10-24T09:49:00">9:49 pm</abbr>
```

You can then obtain the content of this using:

```
{{ select(grab('my-blog-post.md').as_html(),'abbr.published').attr('title') }}
```
