#!/usr/bin/env python3
"""
Resume Serbian Law Scraping
Quick restart script to continue from where we left off
"""

import sys
import os
import json
import time
from datetime import datetime

# Add Scrapling to path
sys.path.append('/home/kaizenlinux/.openclaw/workspace/scrapling-implementation')
from scrapling_integration import AdvancedScraper

def get_remaining_laws():
    """Find laws that haven't been scraped yet"""
    # Load all law URLs
    with open('data/law_links.json', 'r') as f:
        all_laws = json.load(f)
    
    # Get existing files
    laws_dir = 'data/laws'
    existing_files = set()
    if os.path.exists(laws_dir):
        for f in os.listdir(laws_dir):
            if f.endswith('.json'):
                existing_files.add(f.replace('.json', ''))
    
    # Find remaining URLs
    remaining_laws = []
    for url in all_laws:
        slug = os.path.basename(url).replace('.html', '')
        if slug not in existing_files:
            remaining_laws.append(url)
    
    return remaining_laws

def scrape_law(scraper, url):
    """Scrape a single law with our existing logic"""
    slug = os.path.basename(url).replace('.html', '')
    output_file = f"data/laws/{slug}.json"
    
    if os.path.exists(output_file):
        return True, f"Already exists"
    
    try:
        result = scraper.scrape_url(url)
        
        if not result.success:
            return False, f"Failed to fetch: {result.error}"
        
        # Quick parse for title and articles
        content = result.content
        
        # Extract title
        import re
        title_match = re.search(r'<h[1-3][^>]*>([^<]+)</h[1-3]>', content, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else "UNKNOWN LAW"
        
        # Extract gazette
        gazette_patterns = [
            r'"Sl\.\s*glasnik[^"]*"[^)]*',
            r'Sl\.\s*glasnik[^)]+\)',
            r'"Službeni glasnik[^"]*"'
        ]
        gazette = ""
        for pattern in gazette_patterns:
            gazette_match = re.search(pattern, content, re.IGNORECASE)
            if gazette_match:
                gazette = gazette_match.group(0).strip('"()').strip()
                break
        
        # Extract articles with simple pattern
        article_pattern = r'(?:Član|Član)\s+(\d+[a-z]?)\s*\.?\s*(.*?)(?=(?:Član|Član)\s+\d+|$)'
        articles = []
        
        matches = re.finditer(article_pattern, content, re.DOTALL | re.IGNORECASE)
        for match in matches:
            article_num = match.group(1)
            article_text = match.group(2).strip()
            # Clean up
            article_text = re.sub(r'<[^>]+>', '', article_text)
            article_text = re.sub(r'\s+', ' ', article_text).strip()
            
            if len(article_text) > 10:
                articles.append({
                    "number": article_num,
                    "text": article_text,
                    "chapter": ""
                })
        
        # If no articles found, try simpler pattern
        if not articles:
            simple_pattern = r'(\d+)\.\s*([^\\n]+(?:\\n[^\\n]+)*?)(?=\d+\.|$)'
            simple_matches = re.finditer(simple_pattern, content, re.MULTILINE)
            
            for match in simple_matches:
                article_num = match.group(1)
                article_text = match.group(2).strip()
                article_text = re.sub(r'<[^>]+>', '', article_text)
                article_text = re.sub(r'\s+', ' ', article_text).strip()
                
                if len(article_text) > 20:
                    articles.append({
                        "number": article_num,
                        "text": article_text,
                        "chapter": ""
                    })
        
        # Create law data
        law_data = {
            "slug": slug,
            "title": title.strip().upper(),
            "gazette": gazette,
            "source_url": url,
            "scraped_at": datetime.now().isoformat(),
            "article_count": len(articles),
            "articles": articles
        }
        
        # Save to file
        os.makedirs('data/laws', exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(law_data, f, indent=2, ensure_ascii=False)
        
        return True, f"{len(articles)} articles"
        
    except Exception as e:
        return False, f"Error: {str(e)}"

def main():
    print("🔄 RESUMING SERBIAN LAWS SCRAPING")
    print("=" * 50)
    
    # Initialize scraper
    scraper = AdvancedScraper(use_stealth=True, timeout=30)
    
    # Get remaining laws
    remaining_laws = get_remaining_laws()
    total_remaining = len(remaining_laws)
    
    print(f"📊 Found {total_remaining} laws remaining to scrape")
    
    if total_remaining == 0:
        print("✅ All laws already scraped!")
        return
    
    print("🚀 Starting resume scraping...")
    print("=" * 50)
    
    successful = 0
    failed = 0
    
    for i, url in enumerate(remaining_laws, 1):
        slug = os.path.basename(url).replace('.html', '')
        print(f"[{i:4d}/{total_remaining}] {slug[:50]:<50}", end=" ")
        
        success, message = scrape_law(scraper, url)
        if success:
            successful += 1
            print(f"✅ {message}")
        else:
            failed += 1
            print(f"❌ {message}")
        
        # Progress updates
        if i % 25 == 0:
            print(f"📊 Progress: {i}/{total_remaining} ({i/total_remaining*100:.1f}%) | Success: {successful} | Failed: {failed}")
            print(f"   📁 Total laws now: {687 + successful} | Remaining: {total_remaining - i}")
        
        # Respectful delay
        time.sleep(2.5)
        
        # Optional: Remove this break to process all laws
        # if i >= 20:  # Process 20 laws first to test
        #     print("\\n⏸️ Test batch complete - 20 laws processed")
        #     break
    
    print("=" * 50)
    print(f"📊 Resume batch results:")
    print(f"   ✅ Successful: {successful}")
    print(f"   ❌ Failed: {failed}")
    print(f"   📈 Success rate: {successful/(successful+failed)*100:.1f}%")

if __name__ == "__main__":
    main()