import sys

sys.path.insert(0, '.')
from sources.opentwitter import opentwitter_client

print('twitter leg enabled:', opentwitter_client.enabled())
