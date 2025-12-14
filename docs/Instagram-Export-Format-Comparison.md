# Instagram Export Format Comparison

This document compares the structure and data of two Instagram export formats:

- **Legacy Format (2022)**: `instagram-username-20221116`
- **Current Format (2025)**: `instagram-username-20251007`

---

## Overview

Instagram changed their data export format between 2022 and 2025. The key structural change is the introduction of the `your_instagram_activity/` directory, which consolidates activity-related data including media metadata HTML files.

| Aspect | 2022 Export | 2025 Export |
|--------|-------------|-------------|
| Entry Point | `index.html` | `start_here.html` |
| Metadata Location | `content/` | `your_instagram_activity/media/` |
| Messages Location | `messages/` | `your_instagram_activity/messages/` |
| Media Location | `media/` | `media/` + `public-media/` |
| Activity Directory | Not present | `your_instagram_activity/` |

---

## Directory Structure Comparison

### 2022 Export (Legacy Format)

```
instagram-username-20221116/
├── index.html                          # Main entry point
├── ads_and_businesses/
│   ├── advertisers_using_your_activity_or_information.html
│   └── information_you've_submitted_to_advertisers.html
├── ads_and_topics/
│   ├── posts_viewed.html
│   └── suggested_accounts_viewed.html
├── apps_and_websites/
├── autofill_information/
├── avatars_store/
├── comments/
│   ├── post_comments.html
│   └── reels_comments.html
├── contacts/
│   └── synced_contacts.html
├── content/                            # METADATA HTML FILES HERE
│   ├── archived_posts.html
│   ├── igtv_videos.html
│   ├── posts_1.html
│   ├── profile_photos.html
│   └── stories.html
├── device_information/
├── digital_wallets/
├── events/
├── files/
│   ├── fb_logo.png
│   └── Instagram-Logo.png
├── followers_and_following/
│   ├── blocked_accounts.html
│   ├── following.html
│   └── ...
├── fundraisers/
├── guides/
├── information_about_you/
├── likes/
├── login_and_account_creation/
├── loyalty_accounts/
├── media/                              # MEDIA FILES HERE
│   ├── archived_posts/
│   │   └── YYYYMM/
│   ├── igtv/
│   │   └── YYYYMM/
│   ├── other/
│   ├── posts/
│   │   └── YYYYMM/
│   └── stories/
│       └── YYYYMM/
├── media_settings/
├── messages/                           # MESSAGES HERE (root level)
│   ├── chats.html
│   ├── inbox/
│   │   └── {conversation}/
│   │       └── message_1.html
│   ├── message_requests/
│   └── secret_conversations.html
├── monetization/
├── past_instagram_insights/
├── personal_information/
├── recent_searches/
├── reports/
├── saved/
├── shopping/
├── story_sticker_interactions/
└── your_topics/
```

### 2025 Export (Current Format)

```
instagram-username-20251007/
├── start_here.html                     # Main entry point (renamed)
├── ads_information/
│   ├── ads_and_topics/
│   │   ├── ads_clicked.html
│   │   ├── ads_viewed.html
│   │   ├── posts_viewed.html
│   │   └── ...
│   ├── advertising/
│   └── instagram_ads_and_businesses/
├── apps_and_websites_off_of_instagram/
│   └── apps_and_websites/
├── connections/
│   ├── contacts/
│   │   └── synced_contacts.html
│   └── followers_and_following/
│       ├── blocked_profiles.html
│       ├── close_friends.html
│       ├── followers_1.html
│       └── ...
├── files/
│   └── Instagram-Logo.png
├── logged_information/
│   ├── link_history/
│   ├── past_instagram_insights/
│   ├── policy_updates_and_permissions/
│   └── recent_searches/
├── media/                              # MEDIA FILES HERE
│   ├── archived_posts/
│   │   └── YYYYMM/
│   ├── other/
│   ├── posts/
│   │   └── YYYYMM/
│   ├── profile/
│   ├── reels/
│   └── stories/
├── messages/                           # Legacy compatibility (may be empty/link)
├── personal_information/
│   ├── autofill_information/
│   ├── device_information/
│   ├── information_about_you/
│   ├── loyalty_accounts/
│   └── personal_information/
├── preferences/
│   ├── settings/
│   └── your_topics/
├── public-media/                       # Duplicate/organized media
│   ├── archived_posts/
│   ├── other/
│   ├── posts/
│   ├── profile/
│   ├── reels/
│   └── stories/
├── security_and_login_information/
│   └── login_and_profile_creation/
└── your_instagram_activity/            # NEW: Activity container
    ├── ai/
    ├── avatars_store/
    ├── comments/
    ├── edits/
    ├── events/
    ├── fundraisers/
    ├── gifts/
    ├── likes/
    ├── media/                          # METADATA HTML FILES HERE
    │   ├── archived_posts.html
    │   ├── other_content.html
    │   ├── posts_1.html
    │   ├── profile_photos.html
    │   ├── reels.html
    │   └── stories.html
    ├── messages/                       # MESSAGES HERE (nested)
    │   ├── ai_conversations/
    │   ├── chats.html
    │   ├── inbox/
    │   │   └── {conversation}/
    │   │       └── message_N.html
    │   ├── message_requests/
    │   └── secret_conversations.html
    ├── meta_spark/
    ├── monetization/
    ├── other_activity/
    ├── quicksnaps/
    ├── reports/
    ├── saved/
    ├── shared_access/
    ├── shopping/
    ├── story_interactions/
    ├── subscriptions/
    └── threads/
```

---

## Key Differences

### 1. Entry Point

| 2022 | 2025 |
|------|------|
| `index.html` | `start_here.html` |

### 2. Media Metadata Location

**2022**: Metadata HTML files are in `content/` at root level:

- `content/posts_1.html`
- `content/stories.html`
- `content/archived_posts.html`

**2025**: Metadata HTML files moved to `your_instagram_activity/media/`:

- `your_instagram_activity/media/posts_1.html`
- `your_instagram_activity/media/stories.html`
- `your_instagram_activity/media/archived_posts.html`
- `your_instagram_activity/media/reels.html` (new)

### 3. Messages Location

**2022**: Messages at root level:

- `messages/inbox/{conversation}/message_N.html`

**2025**: Messages nested under activity:

- `your_instagram_activity/messages/inbox/{conversation}/message_N.html`
- Added `ai_conversations/` for Meta AI chats
- Added `your_chat_information.html`

### 4. Directory Reorganization

| Category | 2022 Location | 2025 Location |
|----------|---------------|---------------|
| Ads data | `ads_and_businesses/`, `ads_and_topics/` | `ads_information/ads_and_topics/`, `ads_information/instagram_ads_and_businesses/` |
| Contacts | `contacts/` | `connections/contacts/` |
| Followers | `followers_and_following/` | `connections/followers_and_following/` |
| Search history | `recent_searches/` | `logged_information/recent_searches/` |
| Login info | `login_and_account_creation/` | `security_and_login_information/login_and_profile_creation/` |
| Comments | `comments/` | `your_instagram_activity/comments/` |
| Likes | `likes/` | `your_instagram_activity/likes/` |
| Saved | `saved/` | `your_instagram_activity/saved/` |

### 5. New Content Types (2025)

The 2025 format includes additional content types:

- `your_instagram_activity/ai/` - AI-related activity
- `your_instagram_activity/edits/` - Edit history
- `your_instagram_activity/gifts/` - Digital gifts
- `your_instagram_activity/quicksnaps/` - Quick snap content
- `your_instagram_activity/threads/` - Threads app integration
- `your_instagram_activity/subscriptions/` - Creator subscriptions
- `media/reels/` - Reels content (separate from IGTV)
- `public-media/` - Organized public media directory

---

## HTML Metadata Format

Both formats use the same HTML structure for posts metadata. The CSS classes and DOM structure are identical.

### Post Container Structure

```html
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
    <!-- Caption (optional) -->
    <div class="_3-95 _2pim _a6-h _a6-i">Post caption text</div>
    
    <!-- Media content -->
    <div class="_3-95 _a6-p">
        <a target="_blank" href="media/posts/YYYYMM/filename.jpg">
            <img src="media/posts/YYYYMM/filename.jpg" class="_a6_o _3-96" />
        </a>
        
        <!-- Optional: GPS coordinates -->
        <table style="table-layout: fixed;">
            <tr><td colspan="2" class="_2pin _a6_q">
                <div class="_a6-q">Latitude</div>
                <div><div class="_a6-q">33.7566</div></div>
            </td></tr>
            <tr><td colspan="2" class="_2pin _a6_q">
                <div class="_a6-q">Longitude</div>
                <div><div class="_a6-q">-84.3889</div></div>
            </td></tr>
        </table>
        
        <!-- Optional: Tagged users -->
        <table style="table-layout: fixed;">
            <tr><td colspan="2" class="_2pin _a6_q">
                <div class="_a6-q">Tagged users</div>
                <div><div class="_a6-q">username (Tagged, 0.00, 0.00)</div></div>
            </td></tr>
        </table>
    </div>
    
    <!-- Timestamp -->
    <div class="_3-94 _a6-o">Aug 17, 2025 11:23 am</div>
</div>
```

### Key CSS Classes

| Class | Purpose |
|-------|---------|
| `pam _3-95 _2ph- _a6-g uiBoxWhite noborder` | Post container |
| `_3-95 _2pim _a6-h _a6-i` | Caption/title |
| `_3-95 _a6-p` | Media content wrapper |
| `_a6_o _3-96` | Media element (image/video) |
| `_3-94 _a6-o` | Timestamp |
| `_2pin _a6_q` | Metadata cell (GPS, tags) |

### Timestamp Formats

Both formats use the same timestamp format in the HTML:

- `Aug 17, 2025 11:23 am`
- `Sep 8, 2016, 4:36 PM`

Note: The 2022 format includes a comma after the day, while 2025 may not. Both should be handled.

---

## Media File Organization

Both formats organize media files identically in `YYYYMM` subdirectories:

```
media/
├── posts/
│   ├── 201609/
│   │   └── 40388200_717815925220920_6413713794563833856_n_17842207189193574.jpg
│   ├── 202508/
│   │   └── 17852238405523714.jpg
│   └── ...
├── stories/
│   └── YYYYMM/
├── archived_posts/
│   └── YYYYMM/
└── ...
```

Media filenames follow Instagram's internal naming convention with numeric IDs.

---

## Implications for Memoria

### Instagram Public Media Processor

The current processor expects:

1. `media/posts/` or `media/archived_posts/` with YYYYMM folders (detection)
2. `your_instagram_activity/media/*.html` for metadata (preprocessing)

**2022 exports fail** because:

- Detection passes (media structure exists)
- Preprocessing fails (no `your_instagram_activity/media/` directory)

### Recommended Solution

To support legacy exports, the processor would need to:

1. Check for `content/*.html` as a fallback metadata location
2. Map the legacy paths appropriately:
   - `content/posts_1.html` -> post metadata
   - `content/stories.html` -> story metadata
   - etc.

### Instagram Messages Processor

Similar issue with messages location:

- 2022: `messages/inbox/`
- 2025: `your_instagram_activity/messages/inbox/`

---

## Data Availability Comparison

| Data Type | 2022 | 2025 |
|-----------|------|------|
| Posts with captions | Yes | Yes |
| GPS coordinates | Yes | Yes |
| Tagged users | Yes | Yes |
| Timestamps | Yes | Yes |
| Stories | Yes | Yes |
| Archived posts | Yes | Yes |
| IGTV | Yes | Merged into reels |
| Reels | No | Yes |
| Direct messages | Yes | Yes |
| AI conversations | No | Yes |
| Comments made | Yes | Yes |
| Likes given | Yes | Yes |
| Profile photos | Yes | Yes |
| Threads data | No | Yes |

---

## References

- Instagram Data Download: <https://www.instagram.com/download/request/>
- Memoria Processors: `processors/instagram_public_media/`, `processors/instagram_messages/`
