import os
import json
import asyncio
import urllib.request
import urllib.parse
from openai import AsyncOpenAI
import logging

logger = logging.getLogger(__name__)

def fetch_song_details(full_song_name):
    """
    Fetch song details from iTunes API using the full song name.
    """
    logger.info(f"Fetching iTunes data for: {full_song_name}")
    encoded_query = urllib.parse.quote(full_song_name)
    url = f"https://itunes.apple.com/search?term={encoded_query}&entity=song&limit=1"
    
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            results = data.get('results', [])
            if not results:
                return None
            top = results[0]
            return {
                "artist": top.get('artistName', 'Unknown Artist'),
                "track": top.get('trackName', 'Unknown Track'),
                "genre": top.get('primaryGenreName', 'Unknown Genre'),
                "release_date": top.get('releaseDate', 'Unknown Date')[:10]
            }
    except Exception as e:
        logger.error(f"iTunes fetch failed: {e}")
        return None

async def generate_caption_async(song_name, itunes_data, username, template_path="caption.txt"):
    """
    Generate an SEO-optimized caption for the given song using Groq API.
    """
    api_key = os.getenv("GROQ_API_KEY")
    
    # Read the baseline template
    template_content = ""
    if os.path.exists(template_path):
        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

    if not api_key:
        logger.error("GROQ_API_KEY is missing. Returning default caption.")
        if template_content:
            # Replace placeholder if any
            return template_content.replace("@SO9iC", f"@{username}")
        return "GROQ_API_KEY missing. Caption could not be generated."

    client = AsyncOpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=api_key
    )
    
    artist = itunes_data.get('artist') if itunes_data else 'Unknown'
    track = itunes_data.get('track') if itunes_data else song_name
    genre = itunes_data.get('genre') if itunes_data else 'Pop/Hip-Hop'
    release_date = itunes_data.get('release_date') if itunes_data else 'Unknown'
            
    # Build prompt
    prompt = f"""You are an expert social media manager specializing in YouTube/TikTok audio edits (slowed, reverb, sped up, aesthetic edits).
The user is making an audio edit video for the following song: "{song_name}"

=== VERIFIED ITUNES DETAILS ===
Artist: {artist}
Track: {track}
Genre: {genre}
Released: {release_date}
===============================

Here is a highly effective, natural-sounding template for the caption that weaves in SEO keywords smoothly into a story-like paragraph under 'Audio Details & Usage':

--- TEMPLATE START ---
{template_content}
--- TEMPLATE END ---

YOUR TASK:
Rewrite the caption using the exact same structure as the TEMPLATE, but customize ALL the text, keywords, tags, and paragraphs specifically for the song "{track}" by {artist}.
1. Keep the "Give Credit", "Social Media", and "Copyright & Fair Use Notice" sections exactly the same format, but you can leave them untouched. 
2. Replace "@SO9iC" where appropriate with "@{username}".
3. Under "Audio Details & Usage", write a completely new, 1-2 paragraph natural, engaging "yap" about the song. Weave in dynamic keywords (aesthetic, slowed, sped up, viral, tiktok audio, edit audio) very smoothly just like the template did for Ice Spice.
4. Update the "Tags & Search Keywords" section with highly relevant tags and long-tail keywords for this specific artist and song.

Output ONLY the final caption text. Do not include markdown code block backticks around it.
"""

    try:
        response = await client.chat.completions.create(
            model="openai/gpt-oss-120b", 
            messages=[
                {"role": "system", "content": "You are a caption generator. Output ONLY the caption. Do not add any conversational text, greetings, or acknowledge these instructions. If the user provides a template, just output the final rewritten text matching the template structure."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=2048
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Groq API generation failed: {e}")
        return "Failed to generate caption due to API error."
