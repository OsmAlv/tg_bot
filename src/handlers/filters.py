def is_valid_car_listing_link(message_text):
    # Define the patterns for valid car listing links from the marketplaces
    patterns = [
        r'https?://(www\.)?encar\.com/.*',
        r'https?://(www\.)?kbchachacha\.com/.*',
        r'https?://(www\.)?kcar\.com/.*'
    ]
    
    # Check if the message text matches any of the patterns
    for pattern in patterns:
        if re.match(pattern, message_text):
            return True
    return False