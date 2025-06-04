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
    'page_id': '61576451374046',
    'post_id': '122110299680881712',
    'access_token': 'EAAWySti66ogBOZCXuOzanKPC6ZB3BJNCIZB7mfL8FJxZCFOHgcqQFeovWOp9BO5GsvXguYFoef65RexlBV5NJmacfqSK12zvxxGISRKZBOZBQopDHYfa6AWwD0HzfAeRno3Ax0FxZCiPaz4MMr8WZCvXt2XBvX0IgCAZACnMXjILWGKZA0tZAKSO4ZCiZB0yPXNfpbYxZBcEYY7zYd',
    'poll_interval': 5,
    'api_version': 'v19.0',
    'max_retries': 3,
    'retry_delay': 5,
    'data_file': os.path.join(os.getcwd(), 'facebook_comments_data.xlsx'),
    'state_file': os.path.join(os.getcwd(), 'last_comment_state.json'),
    'comment_fields': 'id,from{name},created_time,message',
    'post_fields': 'message'
}

def load_last_state() -> Dict:
    """Load the last processed comment ID, timestamp, and post description"""
    default_state = {'last_comment_id': None, 'last_comment_time': None, 'post_description': None}
    if os.path.exists(CONFIG['state_file']):
        try:
            with open(CONFIG['state_file'], 'r') as f:
                state = json.load(f)
                return state if 'last_comment_id' in state and 'last_comment_time' in state else default_state
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error loading state file: {e}. Using defaults.")
    return default_state

def save_last_state(last_comment_id: str, last_comment_time: str, post_description: str) -> bool:
    """Save the last processed comment ID, timestamp, and post description"""
    try:
        with open(CONFIG['state_file'], 'w') as f:
            json.dump({
                'last_comment_id': last_comment_id,
                'last_comment_time': last_comment_time,
                'post_description': post_description
            }, f)
        logger.info(f"Saved state with last comment ID: {last_comment_id}, time: {last_comment_time}, post description: {post_description[:50] + '...' if post_description else 'None'}")
        return True
    except IOError as e:
        logger.error(f"Error saving state: {e}")
        return False

def fetch_post_description() -> Optional[str]:
    """Fetch the post description from the Facebook API"""
    url = f'https://graph.facebook.com/{CONFIG["api_version"]}/{CONFIG["page_id"]}_{CONFIG["post_id"]}'
    params = {
        'fields': CONFIG['post_fields'],
        'access_token': CONFIG['access_token']
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        description = data.get('message', '')
        if description:
            logger.info(f"Successfully fetched post description: {description[:50] + '...' if len(description) > 50 else description}")
        else:
            logger.warning("Post description is empty or missing")
        return description if description else None
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch post description: {e}")
        return None

def fetch_comments(last_comment_time: Optional[str] = None) -> List[Dict]:
    """Fetch comments from Facebook API with pagination, using timestamp for new comments"""
    base_url = f'https://graph.facebook.com/{CONFIG["api_version"]}/{CONFIG["page_id"]}_{CONFIG["post_id"]}/comments'
    params = {
        'fields': CONFIG['comment_fields'],
        'access_token': CONFIG['access_token'],
        'limit': 100,
        'order': 'chronological'
    }
    if last_comment_time:
        try:
            # Try parsing with ISO format (with timezone)
            dt = datetime.strptime(last_comment_time, '%Y-%m-%dT%H:%M:%S%z')
            params['since'] = int(dt.timestamp())
        except ValueError:
            try:
                # Fallback to non-timezone format
                dt = datetime.strptime(last_comment_time, '%Y-%m-%d %H:%M:%S')
                # Assume UTC for consistency
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
            attempt = 1  # Reset retry counter on success
            logger.debug(f"Fetched {len(comments)} comments from page")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:  # Rate limiting
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

    logger.info(f"Fetched {len(all_comments)} new comments")
    return all_comments

def process_comments(comments: List[Dict], post_description: str) -> List[Dict]:
    """Process comments into a structured format, including post description"""
    processed = []
    for comment in comments:
        try:
            comment_id = str(comment.get('id', '')).strip()
            if not comment_id:
                continue
            created_time = comment.get('created_time', '')
            # Ensure created_time is in ISO format with timezone for consistency
            try:
                dt = datetime.strptime(created_time, '%Y-%m-%dT%H:%M:%S%z')
                formatted_time = dt.strftime('%Y-%m-%dT%H:%M:%S%z')
            except ValueError:
                formatted_time = created_time  # Fallback to raw value if parsing fails
            processed.append({
                'id': comment_id,
                'name': comment.get('from', {}).get('name', 'Unknown'),
                'time': formatted_time,
                'message': comment.get('message', '[No text]'),
                'post_description': post_description or '[No description]'
            })
        except Exception as e:
            logger.error(f"Error processing comment {comment.get('id', 'unknown')}: {e}")
    return processed

def save_to_excel(new_comments: List[Dict]) -> Optional[tuple]:
    """Save new comments to Excel file, appending to existing comments without duplicates"""
    if not new_comments:
        logger.info("No new comments to save")
        return None

    df_new = pd.DataFrame(new_comments)
    df_new['time'] = pd.to_datetime(df_new['time'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')

    try:
        # Load existing comments if file exists
        if os.path.exists(CONFIG['data_file']):
            df_existing = pd.read_excel(CONFIG['data_file'], engine='openpyxl')
            existing_ids = set(df_existing['id'].astype(str))
            # Filter out duplicates based on comment ID
            df_new = df_new[~df_new['id'].astype(str).isin(existing_ids)]
        else:
            df_existing = pd.DataFrame(columns=['id', 'name', 'time', 'message', 'post_description'])

        if not df_new.empty:
            # Append new comments to existing ones
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_excel(CONFIG['data_file'], index=False, engine='openpyxl')
            logger.info(f"Saved {len(df_new)} new comments to {CONFIG['data_file']}")
            # Return the last comment ID and time
            return df_new.iloc[-1]['id'], df_new.iloc[-1]['time']
        else:
            logger.info("No new comments after duplicate check")
    except Exception as e:
        logger.error(f"Error saving to Excel: {e}")

    return None

def test_api_connection() -> bool:
    """Test API connection"""
    url = f'https://graph.facebook.com/{CONFIG["api_version"]}/{CONFIG["page_id"]}_{CONFIG["post_id"]}'
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
    logger.info("Starting real-time Facebook comments fetcher...")

    try:
        import openpyxl
    except ImportError:
        logger.error("Required package 'openpyxl' is not installed. Install with: pip install openpyxl")
        return

    if not test_api_connection():
        logger.error("Cannot connect to Facebook API. Check credentials and network.")
        return

    state = load_last_state()
    post_description = state.get('post_description')

    # Fetch post description if not already stored
    if not post_description:
        post_description = fetch_post_description()
        if post_description is None:
            logger.warning("Continuing without post description due to fetch failure")

    while True:
        logger.info(f"Checking for new comments at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")

        # Fetch comments since last processed comment time
        comments = fetch_comments(state.get('last_comment_time'))
        if comments:
            processed = process_comments(comments, post_description)
            if processed:
                last_info = save_to_excel(processed)
                if last_info:
                    last_comment_id, last_comment_time = last_info
                    # Only save post_description if it exists
                    save_last_state(last_comment_id, last_comment_time, post_description if post_description else state.get('post_description'))
                    state['last_comment_id'] = last_comment_id
                    state['last_comment_time'] = last_comment_time
                    state['post_description'] = post_description if post_description else state.get('post_description')
                    logger.info(f"Updated state with new last comment ID: {last_comment_id}, time: {last_comment_time}")
            else:
                logger.info("No comments processed")
        else:
            logger.info("No new comments found")

        logger.info(f"Waiting {CONFIG['poll_interval']} seconds for next check...")
        time.sleep(CONFIG['poll_interval'])

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nStopping comment fetcher...")