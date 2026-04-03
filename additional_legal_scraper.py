#!/usr/bin/env python3
"""
Additional Serbian Legal Documents Scraper
Scrapes legal content beyond laws: court practice, legal advice, forms, guides
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

class AdditionalLegalScraper:
    def __init__(self):
        self.scraper = AdvancedScraper(use_stealth=True, timeout=30)
        self.base_url = "https://www.paragraf.rs"
        self.output_dir = "/home/kaizenlinux/Projects/Project_02/lexardor-v2/data/additional-documents"
        self.delay = 2.5  # Respectful delay
        
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Document categories we discovered
        self.categories = {
            'legal_training': {
                'urls': [],
                'pattern': '/savetovanja_strane/',
                'description': 'Legal Training and Seminars'
            },
            'legal_forms': {
                'urls': [],
                'pattern': '/dokumenti/',
                'description': 'Legal Forms and Documents'
            },
            'legal_guides': {
                'urls': [],
                'pattern': '/strane/',
                'description': 'Legal Guides and References'
            },
            'legal_handbooks': {
                'urls': [],
                'pattern': '/prirucnici/',
                'description': 'Legal Handbooks'
            },
            'legal_news': {
                'urls': [],
                'pattern': '/dnevne-vesti/',
                'description': 'Legal News and Updates'
            },
            'court_practice': {
                'urls': ['http://bg.ap.sud.rs/sekcija/91/sudska-praksa.php'],
                'pattern': '/praksa/',
                'description': 'Court Practice and Decisions'
            }
        }
    
    def discover_documents(self):
        """Discover all additional legal documents from paragraf.rs"""
        print("🔍 DISCOVERING ADDITIONAL LEGAL DOCUMENTS")
        print("=" * 60)
        
        # Get main page links
        result = self.scraper.scrape_url('https://www.paragraf.rs/')
        if not result.success:
            print("❌ Could not access main page")
            return
        
        all_links = [link for link in result.links if 'paragraf.rs' in link and '/propisi/' not in link]
        
        # Categorize links
        for category_name, category_info in self.categories.items():
            if category_name == 'court_practice':  # Skip, already have URLs
                continue
                
            pattern = category_info['pattern']
            category_links = [link for link in all_links if pattern in link]
            category_info['urls'] = category_links
            
            print(f"📁 {category_info['description']}: {len(category_links)} documents")
            
        print(f"\n📊 Total additional documents discovered: {sum(len(cat['urls']) for cat in self.categories.values())}")
    
    def extract_slug_from_url(self, url):
        """Extract slug from URL for filename"""
        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')
        if path_parts:
            return '_'.join(path_parts).replace('.html', '').replace('.php', '')
        return 'unknown_document'
    
    def parse_legal_document(self, content, url):
        """Parse legal document content"""
        try:
            # Extract title
            title_patterns = [
                r'<h1[^>]*>([^<]+)</h1>',
                r'<h2[^>]*>([^<]+)</h2>',
                r'<title>([^<]+)</title>'
            ]
            
            title = "Unknown Document"
            for pattern in title_patterns:
                title_match = re.search(pattern, content, re.IGNORECASE)
                if title_match:
                    title = title_match.group(1).strip()
                    break
            
            # Extract main content (remove navigation, headers, footers)
            # Look for main content area
            content_patterns = [
                r'<div[^>]*class[^>]*content[^>]*>(.*?)</div>',
                r'<article[^>]*>(.*?)</article>',
                r'<main[^>]*>(.*?)</main>',
                r'<div[^>]*id[^>]*main[^>]*>(.*?)</div>'
            ]
            
            main_content = content
            for pattern in content_patterns:
                match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
                if match:
                    main_content = match.group(1)
                    break
            
            # Clean up content - remove HTML tags but keep structure
            clean_content = re.sub(r'<script[^>]*>.*?</script>', '', main_content, flags=re.DOTALL)
            clean_content = re.sub(r'<style[^>]*>.*?</style>', '', clean_content, flags=re.DOTALL)
            clean_content = re.sub(r'<[^>]+>', ' ', clean_content)
            clean_content = re.sub(r'\s+', ' ', clean_content).strip()
            
            # Extract key sections or paragraphs
            sections = []
            if len(clean_content) > 500:
                # Split into meaningful paragraphs
                paragraphs = [p.strip() for p in clean_content.split('.') if len(p.strip()) > 50]
                sections = paragraphs[:20]  # Take first 20 substantial paragraphs
            
            # Determine document type based on URL and content
            url_lower = url.lower()
            if 'savetovanja' in url_lower or 'obuka' in url_lower:
                doc_type = 'training'
            elif 'praksa' in url_lower or 'presude' in url_lower:
                doc_type = 'court_practice'
            elif 'dokument' in url_lower or 'obrazac' in url_lower:
                doc_type = 'form'
            elif 'prirucnik' in url_lower:
                doc_type = 'handbook'
            elif 'vesti' in url_lower:
                doc_type = 'news'
            else:
                doc_type = 'guide'
            
            return {
                "slug": self.extract_slug_from_url(url),
                "title": title,
                "document_type": doc_type,
                "source_url": url,
                "scraped_at": datetime.now().isoformat(),
                "content_length": len(clean_content),
                "content": clean_content,
                "sections": sections
            }
            
        except Exception as e:
            print(f"❌ Error parsing content from {url}: {e}")
            return None
    
    def scrape_document(self, url, category):
        """Scrape a single additional legal document"""
        slug = self.extract_slug_from_url(url)
        output_file = os.path.join(self.output_dir, f"{category}_{slug}.json")
        
        if os.path.exists(output_file):
            return True, "Already scraped"
        
        try:
            result = self.scraper.scrape_url(url)
            
            if not result.success:
                return False, f"Failed to fetch: {result.error}"
            
            # Parse the document
            doc_data = self.parse_legal_document(result.content, url)
            
            if not doc_data:
                return False, "Failed to parse content"
            
            # Add category info
            doc_data['category'] = category
            
            # Save to JSON file
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(doc_data, f, indent=2, ensure_ascii=False)
            
            return True, f"Saved {doc_data['content_length']} chars"
            
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    def scrape_all_additional_documents(self):
        """Scrape all additional legal documents"""
        print("\\n🚀 SCRAPING ADDITIONAL LEGAL DOCUMENTS")
        print("=" * 60)
        
        total_documents = 0
        successful = 0
        failed = 0
        
        for category_name, category_info in self.categories.items():
            if not category_info['urls']:
                continue
                
            print(f"\\n📁 {category_info['description']}")
            print("-" * 40)
            
            for i, url in enumerate(category_info['urls'], 1):
                print(f"[{i:2d}/{len(category_info['urls'])}] ", end="")
                
                success, message = self.scrape_document(url, category_name)
                
                if success:
                    successful += 1
                    print(f"✅ {message} - {url}")
                else:
                    failed += 1
                    print(f"❌ {message} - {url}")
                
                total_documents += 1
                
                # Respectful delay
                if i < len(category_info['urls']):
                    time.sleep(self.delay)
        
        print("=" * 60)
        print(f"📊 ADDITIONAL DOCUMENTS SCRAPING RESULTS:")
        print(f"   ✅ Successful: {successful}")
        print(f"   ❌ Failed: {failed}")
        print(f"   📈 Success rate: {successful/total_documents*100:.1f}%")
        print(f"   📁 Documents saved to: {self.output_dir}")
        
        return successful, failed

def main():
    """Main execution"""
    scraper = AdditionalLegalScraper()
    
    print("📚 SERBIAN ADDITIONAL LEGAL DOCUMENTS SCRAPER")
    print("Using Scrapling for enhanced extraction")
    print("=" * 60)
    
    # Step 1: Discover documents
    scraper.discover_documents()
    
    # Step 2: Scrape all documents
    successful, failed = scraper.scrape_all_additional_documents()
    
    # Step 3: Final summary
    print("\\n🎉 ADDITIONAL DOCUMENTS SCRAPING COMPLETE!")
    print("=" * 60)
    
    # Count total content
    try:
        total_docs = 0
        total_content = 0
        doc_files = [f for f in os.listdir(scraper.output_dir) if f.endswith('.json')]
        
        for doc_file in doc_files:
            with open(os.path.join(scraper.output_dir, doc_file), 'r') as f:
                doc_data = json.load(f)
                total_content += doc_data.get('content_length', 0)
                total_docs += 1
        
        print(f"📊 Final Statistics:")
        print(f"   📁 Additional documents: {total_docs}")
        print(f"   📄 Total content: {total_content:,} characters")
        print(f"   📈 Average per document: {total_content/total_docs:.0f} chars")
        print(f"   💾 Saved to: {scraper.output_dir}")
        
    except Exception as e:
        print(f"⚠️ Could not calculate final statistics: {e}")
    
    print("\\n🤖 Ready for LexArdor integration alongside laws!")

if __name__ == "__main__":
    main()