import requests
import pandas as pd
import json
from datetime import datetime, timezone
import time
import os
import logging
from typing import Optional, Dict, List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('facebook_comments_fetcher.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    'page_id': '677353305455532',
    'access_token': 'EAAWySti66ogBO9tJ7aos3ZApTXQnrcpeaX6uth6WZB2QnZBlnenLDlU9eq7oR2ZBQEXeOdn5XxVwmCN32off28rEbyKddQkK2R9ucf08hVq0ZBRzbdxdfSmzygQZAiqx5ClhmEwtIYSbJYiVIMFn4dsyVftIaJFPA3iu7VfeslobWXmZAseNC6CJBjG5Af1ZCx7TKZAtBkCcg',
    'poll_interval': 5,
    'api_version': 'v19.0',
    'max_retries': 3,
    'retry_delay': 5,
    'data_file': os.path.join(os.getcwd(), 'facebook_comments_data.xlsx'),
    'state_file': os.path.join(os.getcwd(), 'last_comment_state.json'),
    'comment_fields': 'id,from{name},created_time,message',
    'post_fields': 'id,message,created_time'
}

def load_last_state() -> Dict:
    """Load the last processed comment ID, timestamp, post ID, and post description"""
    default_state = {'last_comment_id': None, 'last_comment_time': None, 'post_id': None, 'post_description': None}
    if os.path.exists(CONFIG['state_file']):
        try:
            with open(CONFIG['state_file'], 'r') as f:
                state = json.load(f)
                return state if all(key in state for key in default_state) else default_state
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error loading state file: {e}. Using defaults.")
    return default_state

def save_last_state(last_comment_id: str, last_comment_time: str, post_id: str, post_description: str) -> bool:
    """Save the last processed comment ID, timestamp, post ID, and post description"""
    try:
        with open(CONFIG['state_file'], 'w') as f:
            json.dump({
                'last_comment_id': last_comment_id,
                'last_comment_time': last_comment_time,
                'post_id': post_id,
                'post_description': post_description
            }, f)
        logger.info(f"Saved state with last comment ID: {last_comment_id}, time: {last_comment_time}, post ID: {post_id}, post description: {post_description[:50] + '...' if post_description else 'None'}")
        return True
    except IOError as e:
        logger.error(f"Error saving state: {e}")
        return False

def fetch_posts() -> List[Dict]:
    """Fetch posts from the page and filter for 'complient' in message"""
    url = f'https://graph.facebook.com/{CONFIG["api_version"]}/{CONFIG["page_id"]}/posts'
    params = {
        'fields': CONFIG['post_fields'],
        'access_token': CONFIG['access_token'],
        'limit': 100
    }
    all_posts = []
    attempt = 1

    while url and attempt <= CONFIG['max_retries']:
        try:
            response = requests.get(url, params=params if url == f'https://graph.facebook.com/{CONFIG["api_version"]}/{CONFIG["page_id"]}/posts' else {}, timeout=30)
            response.raise_for_status()
            data = response.json()
            posts = data.get('data', [])
            # Filter posts containing 'complient' (case-insensitive)
            filtered_posts = [post for post in posts if 'message' in post and 'complient' in post['message'].lower()]
            all_posts.extend(filtered_posts)
            url = data.get('paging', {}).get('next')
            attempt = 1
            logger.debug(f"Fetched {len(posts)} posts, {len(filtered_posts)} with 'complient'")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                retry_after = int(e.response.headers.get('Retry-After', CONFIG['retry_delay']))
                logger.warning(f"Rate limited. Retrying after {retry_after} seconds...")
                time.sleep(retry_after)
                attempt += 1
                continue
            logger.error(f"HTTP Error: {e}")
            break
        except requests.exceptions.RequestException as e:
            if attempt < CONFIG['max_retries']:
                logger.warning(f"Request failed (attempt {attempt}): {e}. Retrying in {CONFIG['retry_delay']} seconds...")
                time.sleep(CONFIG['retry_delay'] * attempt)
                attempt += 1
                continue
            logger.error(f"Max retries reached. Final error: {e}")
            break

    # Sort posts by creation time (newest first)
    all_posts.sort(key=lambda x: datetime.strptime(x['created_time'], '%Y-%m-%dT%H:%M:%S%z'), reverse=True)
    logger.info(f"Found {len(all_posts)} posts containing 'complient'")
    return all_posts

def fetch_comments(post_id: str, last_comment_time: Optional[str] = None) -> List[Dict]:
    """Fetch comments for a specific post from Facebook API with pagination"""
    base_url = f'https://graph.facebook.com/{CONFIG["api_version"]}/{post_id}/comments'
    params = {
        'fields': CONFIG['comment_fields'],
        'access_token': CONFIG['access_token'],
        'limit': 100,
        'order': 'chronological'
    }
    if last_comment_time:
        try:
            dt = datetime.strptime(last_comment_time, '%Y-%m-%dT%H:%M:%S%z')
            params['since'] = int(dt.timestamp())
        except ValueError:
            try:
                dt = datetime.strptime(last_comment_time, '%Y-%m-%d %H:%M:%S')
                dt = dt.replace(tzinfo=timezone.utc)
                params['since'] = int(dt.timestamp())
                logger.info(f"Parsed last_comment_time as non-timezone format: {last_comment_time}")
            except ValueError as e:
                logger.warning(f"Invalid last_comment_time format: {e}. Fetching all comments.")
                params.pop('since', None)

    all_comments = []
    url = base_url
    attempt = 1

    while url and attempt <= CONFIG['max_retries']:
        try:
            response = requests.get(url, params=params if url == base_url else {}, timeout=30)
            response.raise_for_status()
            data = response.json()
            comments = data.get('data', [])
            all_comments.extend(comments)
            url = data.get('paging', {}).get('next')
            attempt = 1
            logger.debug(f"Fetched {len(comments)} comments from post {post_id}")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                retry_after = int(e.response.headers.get('Retry-After', CONFIG['retry_delay']))
                logger.warning(f"Rate limited. Retrying after {retry_after} seconds...")
                time.sleep(retry_after)
                attempt += 1
                continue
            logger.error(f"HTTP Error: {e}")
            break
        except requests.exceptions.RequestException as e:
            if attempt < CONFIG['max_retries']:
                logger.warning(f"Request failed (attempt {attempt}): {e}. Retrying in {CONFIG['retry_delay']} seconds...")
                time.sleep(CONFIG['retry_delay'] * attempt)
                attempt += 1
                continue
            logger.error(f"Max retries reached. Final error: {e}")
            break

    logger.info(f"Fetched {len(all_comments)} new comments for post {post_id}")
    return all_comments

def process_comments(comments: List[Dict], post_description: str, post_id: str) -> List[Dict]:
    """Process comments into a structured format, including post description and ID"""
    processed = []
    for comment in comments:
        try:
            comment_id = str(comment.get('id', '')).strip()
            if not comment_id:
                continue
            created_time = comment.get('created_time', '')
            try:
                dt = datetime.strptime(created_time, '%Y-%m-%dT%H:%M:%S%z')
                formatted_time = dt.strftime('%Y-%m-%dT%H:%M:%S%z')
            except ValueError:
                formatted_time = created_time
            processed.append({
                'id': comment_id,
                'name': comment.get('from', {}).get('name', 'Unknown'),
                'time': formatted_time,
                'message': comment.get('message', '[No text]'),
                'post_id': post_id,
                'post_description': post_description or '[No description]'
            })
        except Exception as e:
            logger.error(f"Error processing comment {comment.get('id', 'unknown')}: {e}")
    return processed

def save_to_excel(new_comments: List[Dict]) -> Optional[tuple]:
    """Save new comments to Excel file, appending without duplicates"""
    if not new_comments:
        logger.info("No new comments to save")
        return None

    df_new = pd.DataFrame(new_comments)
    df_new['time'] = pd.to_datetime(df_new['time'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')

    try:
        if os.path.exists(CONFIG['data_file']):
            df_existing = pd.read_excel(CONFIG['data_file'], engine='openpyxl')
            existing_ids = set(df_existing['id'].astype(str))
            df_new = df_new[~df_new['id'].astype(str).isin(existing_ids)]
        else:
            df_existing = pd.DataFrame(columns=['id', 'name', 'time', 'message', 'post_id', 'post_description'])

        if not df_new.empty:
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_excel(CONFIG['data_file'], index=False, engine='openpyxl')
            logger.info(f"Saved {len(df_new)} new comments to {CONFIG['data_file']}")
            return df_new.iloc[-1]['id'], df_new.iloc[-1]['time'], df_new.iloc[-1]['post_id']
        else:
            logger.info("No new comments after duplicate check")
    except Exception as e:
        logger.error(f"Error saving to Excel: {e}")

    return None

def test_api_connection() -> bool:
    """Test API connection"""
    url = f'https://graph.facebook.com/{CONFIG["api_version"]}/{CONFIG["page_id"]}'
    params = {'fields': 'id', 'access_token': CONFIG['access_token']}
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        logger.info("API connection test successful")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"API connection failed: {e}")
        return False

def main():
    logger.info("Starting real-time Facebook comments fetcher for posts with 'complient'...")

    try:
        import openpyxl
    except ImportError:
        logger.error("Required package 'openpyxl' is not installed. Install with: pip install openpyxl")
        return

    if not test_api_connection():
        logger.error("Cannot connect to Facebook API. Check credentials and network.")
        return

    state = load_last_state()

    while True:
        logger.info(f"Checking for new posts/comments at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")

        # Fetch posts containing 'complient'
        posts = fetch_posts()
        if not posts:
            logger.info("No posts found containing 'complient'")
            time.sleep(CONFIG['poll_interval'])
            continue

        # Get the latest post
        latest_post = posts[0]
        post_id = latest_post['id']
        post_description = latest_post.get('message', '')
        post_time = datetime.strptime(latest_post['created_time'], '%Y-%m-%dT%H:%M:%S%z')

        # Check if this is a new post or same as last processed
        if state.get('post_id') != post_id:
            logger.info(f"New post detected with ID: {post_id}, created at: {post_time}")
            state['last_comment_id'] = None
            state['last_comment_time'] = None
            state['post_id'] = post_id
            state['post_description'] = post_description

        # Fetch comments for the latest post
        comments = fetch_comments(post_id, state.get('last_comment_time'))
        if comments:
            processed = process_comments(comments, post_description, post_id)
            if processed:
                last_info = save_to_excel(processed)
                if last_info:
                    last_comment_id, last_comment_time, last_post_id = last_info
                    save_last_state(last_comment_id, last_comment_time, last_post_id, post_description)
                    state['last_comment_id'] = last_comment_id
                    state['last_comment_time'] = last_comment_time
                    state['post_id'] = last_post_id
                    state['post_description'] = post_description
                    logger.info(f"Updated state with new last comment ID: {last_comment_id}, time: {last_comment_time}, post ID: {last_post_id}")
            else:
                logger.info("No comments processed")
        else:
            logger.info(f"No new comments found for post {post_id}")

        logger.info(f"Waiting {CONFIG['poll_interval']} seconds for next check...")
        time.sleep(CONFIG['poll_interval'])

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nStopping comment fetcher...")