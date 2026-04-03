#!/usr/bin/env python3
"""
Serbian Law Scraper using Scrapling
Scrapes all laws from paragraf.rs and saves as structured JSON
"""

import sys
import os
import json
import re
import time
from datetime import datetime
from urllib.parse import urlparse

# Add Scrapling to path
sys.path.append('/home/kaizenlinux/.openclaw/workspace/scrapling-implementation')
from scrapling_integration import AdvancedScraper

class SerbianLawScraper:
    def __init__(self):
        self.scraper = AdvancedScraper(use_stealth=True, timeout=30)
        self.base_url = "https://www.paragraf.rs"
        self.laws_dir = "/home/kaizenlinux/Projects/Project_02/lexardor-v2/data/laws"
        self.delay = 2.5  # Respectful delay between requests
        
        # Ensure output directory exists
        os.makedirs(self.laws_dir, exist_ok=True)
        
    def extract_slug_from_url(self, url):
        """Extract slug from law URL"""
        return os.path.basename(urlparse(url).path).replace('.html', '')
    
    def parse_law_content(self, content, url):
        """Parse Serbian law content into structured JSON"""
        try:
            # Extract title (usually first heading in ALL CAPS)
            title_match = re.search(r'<h[1-3][^>]*>([^<]+)</h[1-3]>', content, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else "UNKNOWN LAW"
            
            # Extract Službeni glasnik reference
            gazette_patterns = [
                r'"Sl\.\s*glasnik[^"]*"[^)]*',
                r'Sl\.\s*glasnik[^)]+\)',
                r'"Službeni glasnik[^"]*"',
                r'Sl\.\s*list[^)]+\)'
            ]
            
            gazette = ""
            for pattern in gazette_patterns:
                gazette_match = re.search(pattern, content, re.IGNORECASE)
                if gazette_match:
                    gazette = gazette_match.group(0).strip('"()').strip()
                    break
            
            # Parse articles - look for "Član" pattern
            article_pattern = r'(?:Član|Član)\s+(\d+[a-z]?)\s*\.?\s*(.*?)(?=(?:Član|Član)\s+\d+|$)'
            articles = []
            current_chapter = ""
            
            # Find chapters (Roman numerals with titles)
            chapter_pattern = r'([IVX]+)\.\s*([A-ZŠĐČĆŽ\s]+)'
            chapters = re.findall(chapter_pattern, content)
            chapter_map = {}
            for roman, title in chapters:
                chapter_map[roman] = f"{roman}. {title.strip()}"
            
            # Extract all articles
            matches = re.finditer(article_pattern, content, re.DOTALL | re.IGNORECASE)
            
            for match in matches:
                article_num = match.group(1)
                article_text = match.group(2).strip()
                
                # Clean up article text
                article_text = re.sub(r'<[^>]+>', '', article_text)  # Remove HTML tags
                article_text = re.sub(r'\s+', ' ', article_text).strip()  # Normalize whitespace
                
                # Find which chapter this article belongs to
                article_chapter = current_chapter
                for chapter_roman, chapter_title in chapter_map.items():
                    # This is a simple heuristic - could be improved
                    if chapter_title.lower() in article_text[:200].lower():
                        article_chapter = chapter_title
                        break
                
                if article_text and len(article_text) > 10:  # Only add substantial articles
                    articles.append({
                        "number": article_num,
                        "text": article_text,
                        "chapter": article_chapter
                    })
            
            # If no articles found with standard pattern, try alternative patterns
            if not articles:
                # Try simpler patterns
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
            
            return {
                "slug": self.extract_slug_from_url(url),
                "title": title.strip().upper(),
                "gazette": gazette,
                "source_url": url,
                "scraped_at": datetime.now().isoformat(),
                "article_count": len(articles),
                "articles": articles
            }
            
        except Exception as e:
            print(f"❌ Error parsing content: {e}")
            return None
    
    def scrape_single_law(self, url):
        """Scrape a single law and return structured data"""
        print(f"📄 Scraping: {url}")
        
        # Check if already scraped
        slug = self.extract_slug_from_url(url)
        output_file = os.path.join(self.laws_dir, f"{slug}.json")
        
        if os.path.exists(output_file):
            print(f"   ⏭️ Already scraped, skipping")
            return True
        
        try:
            result = self.scraper.scrape_url(url)
            
            if not result.success:
                print(f"   ❌ Failed to fetch: {result.error}")
                return False
            
            # Parse the content
            law_data = self.parse_law_content(result.content, url)
            
            if not law_data:
                print(f"   ❌ Failed to parse content")
                return False
            
            # Save to JSON file
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(law_data, f, indent=2, ensure_ascii=False)
            
            print(f"   ✅ Saved {law_data['article_count']} articles to {slug}.json")
            
            return True
            
        except Exception as e:
            print(f"   ❌ Error scraping: {e}")
            return False
    
    def scrape_laws_from_list(self, law_urls, phase_name=""):
        """Scrape multiple laws with progress tracking"""
        total = len(law_urls)
        successful = 0
        failed = 0
        
        print(f"\\n🚀 Starting {phase_name}")
        print(f"📊 Total laws to scrape: {total}")
        print("=" * 60)
        
        for i, url in enumerate(law_urls, 1):
            print(f"[{i:3d}/{total}] ", end="")
            
            if self.scrape_single_law(url):
                successful += 1
            else:
                failed += 1
            
            # Respectful delay
            if i < total:
                time.sleep(self.delay)
        
        print("=" * 60)
        print(f"📊 {phase_name} Results:")
        print(f"   ✅ Successful: {successful}")
        print(f"   ❌ Failed: {failed}")
        print(f"   📈 Success rate: {successful/total*100:.1f}%")
        
        return successful, failed

def main():
    """Main scraping execution"""
    scraper = SerbianLawScraper()
    
    print("🏛️ Serbian Laws Scraper")
    print("Using Scrapling for 767x faster extraction")
    print("=" * 60)
    
    # Load law lists
    data_dir = "/home/kaizenlinux/Projects/Project_02/lexardor-v2/data"
    
    with open(os.path.join(data_dir, "priority_laws.json"), 'r') as f:
        priority_laws = json.load(f)
    
    with open(os.path.join(data_dir, "law_links.json"), 'r') as f:
        all_laws = json.load(f)
    
    # Phase 1: Priority laws
    successful_priority, failed_priority = scraper.scrape_laws_from_list(
        priority_laws, "PHASE 1: Priority Laws"
    )
    
    # Phase 2: All remaining laws
    remaining_laws = [law for law in all_laws if law not in priority_laws]
    successful_remaining, failed_remaining = scraper.scrape_laws_from_list(
        remaining_laws, "PHASE 2: All Remaining Laws"
    )
    
    # Final summary
    total_successful = successful_priority + successful_remaining
    total_failed = failed_priority + failed_remaining
    total_laws = len(all_laws)
    
    print("\\n" + "=" * 60)
    print("🎉 SERBIAN LAWS SCRAPING COMPLETE!")
    print("=" * 60)
    print(f"📊 Final Results:")
    print(f"   ✅ Total successful: {total_successful}")
    print(f"   ❌ Total failed: {total_failed}")
    print(f"   📈 Overall success rate: {total_successful/total_laws*100:.1f}%")
    print(f"   📁 Laws saved to: {scraper.laws_dir}")
    
    # Count articles
    try:
        total_articles = 0
        law_files = [f for f in os.listdir(scraper.laws_dir) if f.endswith('.json')]
        
        for law_file in law_files:
            with open(os.path.join(scraper.laws_dir, law_file), 'r') as f:
                law_data = json.load(f)
                total_articles += law_data.get('article_count', 0)
        
        print(f"   📋 Total articles extracted: {total_articles:,}")
        print(f"   📚 Average articles per law: {total_articles/len(law_files):.1f}")
        
    except Exception as e:
        print(f"   ⚠️ Could not count articles: {e}")
    
    print("\\n🤖 Ready for LexArdor integration!")

if __name__ == "__main__":
    main()