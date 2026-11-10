from html.parser import HTMLParser

class Validator(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.void_elements = {'meta', 'link', 'img', 'br', 'hr', 'input', 'area', 'base', 'col', 'embed', 'param', 'source', 'track', 'wbr'}
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.void_elements:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.void_elements:
            return
        if not self.stack:
            self.errors.append(f'Unexpected closing tag </{tag}>')
            return
        last = self.stack.pop()
        if last != tag:
            self.errors.append(f'Mismatched tags: expected </{last}>, found </{tag}>')

v = Validator()
with open('webapp/index.html', 'r', encoding='utf-8') as f:
    v.feed(f.read())

if v.stack:
    print('Unclosed tags:', v.stack)
    exit(1)
if v.errors:
    print('Errors:', v.errors)
    exit(1)
print('HTML is 100% valid and balanced!')
