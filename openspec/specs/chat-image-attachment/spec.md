## ADDED Requirements

### Requirement: Image Attachment Input
The system SHALL allow users to attach images in the conversation input area via three methods: paste, click to upload, and drag-and-drop.

#### Scenario: Paste Image
- **WHEN** the user presses Ctrl+V (or Cmd+V) in the conversation input area and the clipboard contains an image
- **THEN** the system adds the image to the attachment list and displays a thumbnail above the input box

#### Scenario: Click to Upload
- **WHEN** the user clicks the attachment button next to the input box and selects one or more images from the file selector
- **THEN** the system adds the selected images to the attachment list and displays thumbnails

#### Scenario: Drag-and-Drop Image
- **WHEN** the user drags an image file into the conversation input area and releases it
- **THEN** the system adds the image to the attachment list and displays a thumbnail; during the drag-in process the input area SHALL highlight to show drop feedback

#### Scenario: Exceeds Maximum Count
- **WHEN** the current attachment count has already reached 5 and the user tries to add another image
- **THEN** the system ignores the new image and the attachment button SHALL become disabled

#### Scenario: Exceeds File Size Limit
- **WHEN** the user adds a single image larger than 5MB
- **THEN** the system rejects the addition and displays an error message to the user

#### Scenario: Remove Attachment
- **WHEN** the user clicks the delete button in the upper right of a thumbnail
- **THEN** the system removes that image from the attachment list and the thumbnail disappears

### Requirement: Send Message with Images
The system SHALL submit attached images together with text content when the user sends a message.

#### Scenario: Send Message with Images
- **WHEN** the user clicks send (or presses Enter) when the attachment list is non-empty
- **THEN** the system combines the text and image base64 data into a multimodal message and sends it; the attachment list SHALL be cleared after sending

#### Scenario: Send Text Only
- **WHEN** the user sends a message when the attachment list is empty
- **THEN** system behavior is consistent with the original plain text send behavior

### Requirement: Image Message Rendering
The system SHALL correctly render images sent by the user in the conversation history, with support for click-to-zoom viewing.

#### Scenario: Immediate Display After Sending
- **WHEN** a message with images is sent successfully
- **THEN** the user's message bubble SHALL display image thumbnails (maximum height 256px), with text content displayed below the images

#### Scenario: Display in History Playback
- **WHEN** the user reloads an existing session
- **THEN** historical messages with images SHALL correctly render the image content

#### Scenario: Click Thumbnail to Zoom
- **WHEN** the user clicks an image thumbnail in the conversation
- **THEN** the system displays the original image in full-screen lightbox mode; clicking the overlay or pressing Esc closes it

#### Scenario: Click Pending-Send Image to Zoom
- **WHEN** the user clicks an attachment thumbnail in the input area
- **THEN** the system displays that image in full-screen lightbox mode; clicking the overlay or pressing Esc closes it
