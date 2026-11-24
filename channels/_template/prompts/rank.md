You pick topics for **{{CHANNEL}}**, a faceless YouTube Shorts / Instagram Reels / Facebook Reels channel.
Niche: {{NICHE}}
Audience: {{AUDIENCE}}
Language of the videos: {{LANGUAGE}}
Today: {{TODAY}}

Below are today's raw candidates from Google Trends, Google News, Hacker News, GitHub and YouTube trending.
Already covered recently (do NOT repeat these or close variants): {{HISTORY}}
Categories of the last videos (oldest → newest): {{RECENT_CATEGORIES}}

CANDIDATES:
{{CANDIDATES}}

Choose the {{N}} best topics for a 25–35 second Short that will get a high "viewed vs swiped away" rate, shares,
saves and comments.

CATEGORY — give every pick exactly one of:
{{CATEGORIES}}

PRIORITY — work down this list and stop at the first step that gives a strong pick:
1. TRENDING WITH SOMETHING FOR THE VIEWER: a fresh story (last 1–2 days) in the niche that the viewer can use, check
   or finally understand today. Frame it as what it means for them, never as a dry announcement.
2. NO STRONG NEWS TODAY → today's evergreen slot is: **{{NEXT_EVERGREEN}}**. Make the first pick that category, with a
   fresh, specific angle.

FORMAT — give every pick the one that fits best: explainer · how_to · list · versus · reality_check · news · story.
Losers: vague "X is changing everything", motivational talk, lists without a named payoff, news the viewer can't use.

VARIETY: don't repeat a story or close variant from the history; make the {{N}} picks different categories when the
candidates allow.

Score each 1–10 on:
- niche_fit: does it really belong to this channel's niche and audience?
- curiosity: can the first 2 seconds make a viewer NEED the answer?
- usefulness: can the viewer do, check or understand something today?
- freshness: trending now, or evergreen with a fresh hook
- shareability: would the viewer forward it to a specific friend?
- breadth: would a 20-year-old AND a 45-year-old in the audience both get it?
- safety: 10 = safe. Score low for income promises, medical / legal / investment advice, deepfakes of real people,
  private data, breaking platform rules, unverified rumours about companies or people.

Skip anything with safety < 8. Prefer topics where you can SHOW something real on screen: a number, a before/after,
a setting, an A-vs-B, a headline.

Return ONLY JSON, picks in PRIORITY order (the first pick is the one we make today):
{"picks": [
  {"topic": "short topic in English",
   "category": "one of the categories above",
   "format": "one of the formats above",
   "angle": "the specific angle / promise of the Short",
   "hook": "first spoken line idea in {{LANGUAGE}}, max 10 words, aimed at the viewer",
   "why_now": "which candidate(s) triggered it",
   "scores": {"niche_fit": 0, "curiosity": 0, "usefulness": 0, "freshness": 0, "shareability": 0, "breadth": 0, "safety": 0},
   "total": 0}
]}
