import re, subprocess
html = open('/tmp/dashboard.html').read()
m = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
if m:
    open('/tmp/test.js','w').write(m.group(1))
    r = subprocess.run(['node','--check','/tmp/test.js'], capture_output=True, text=True)
    print('STDERR:', r.stderr[:500] if r.stderr else 'none')
    print('RC:', r.returncode)
else:
    print('No script tag found')
