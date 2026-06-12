"""Test fal.ai API connectivity for image, video, and audio generation."""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.custom_provider.fal_client import FalClient

# fal.ai API key from database
FAL_KEY = "5e76bd3b-9e62-4cb2-8e5f-ad14daf05fe1:871c614da4602ff4e2acb37c8a4de8b1"


async def test_image():
    """Test image generation with fal-ai/flux/schnell."""
    print("\n=== TEST 1: Image Generation (fal-ai/flux/schnell) ===")
    client = FalClient(FAL_KEY)
    try:
        result = await client.run("fal-ai/flux/schnell", {"prompt": "a beautiful sunset over mountains, photorealistic"})
        print(f"✅ Image SUCCESS")
        print(f"   Response keys: {list(result.keys())}")
        images = result.get("images", result.get("output", {}).get("images", []))
        if images:
            url = images[0].get("url") if isinstance(images[0], dict) else images[0]
            print(f"   Image URL: {url[:100]}...")
        else:
            print(f"   Full response: {str(result)[:500]}")
        return True
    except Exception as e:
        print(f"❌ Image FAILED: {e}")
        return False


async def test_video():
    """Test video generation with fal-ai/wan/v2.1/1.3b/text-to-video."""
    print("\n=== TEST 2: Video Generation (fal-ai/wan/v2.1/1.3b/text-to-video) ===")
    client = FalClient(FAL_KEY)
    try:
        result = await client.run("fal-ai/wan/v2.1/1.3b/text-to-video", {"prompt": "a cat walking gracefully", "duration": 3})
        print(f"✅ Video SUCCESS")
        print(f"   Response keys: {list(result.keys())}")
        video_data = result.get("video", result.get("output", {}).get("video", {}))
        if isinstance(video_data, dict):
            print(f"   Video URL: {video_data.get('url', 'N/A')[:100]}...")
        else:
            print(f"   Full response: {str(result)[:500]}")
        return True
    except Exception as e:
        print(f"❌ Video FAILED: {e}")
        return False


async def test_audio_tts():
    """Test TTS with fal-ai/elevenlabs/tts/turbo-v2.5."""
    print("\n=== TEST 3: Audio TTS (fal-ai/elevenlabs/tts/turbo-v2.5) ===")
    client = FalClient(FAL_KEY)
    try:
        result = await client.run("fal-ai/elevenlabs/tts/turbo-v2.5", {"text": "Hello! This is a test of the fal.ai text to speech system."})
        print(f"✅ Audio TTS SUCCESS")
        print(f"   Response keys: {list(result.keys())}")
        audio_data = result.get("audio", {})
        if isinstance(audio_data, dict):
            print(f"   Audio URL: {audio_data.get('url', 'N/A')[:100]}...")
        else:
            print(f"   Full response: {str(result)[:500]}")
        return True
    except Exception as e:
        print(f"❌ Audio TTS FAILED: {e}")
        return False


async def test_audio_music():
    """Test music generation with fal-ai/ace-step."""
    print("\n=== TEST 4: Audio Music (fal-ai/ace-step) ===")
    client = FalClient(FAL_KEY)
    try:
        result = await client.run("fal-ai/ace-step", {"tags": "lofi, chill, ambient", "duration": 10})
        print(f"✅ Audio Music SUCCESS")
        print(f"   Response keys: {list(result.keys())}")
        audio_data = result.get("audio", {})
        if isinstance(audio_data, dict):
            print(f"   Audio URL: {audio_data.get('url', 'N/A')[:100]}...")
        else:
            print(f"   Full response: {str(result)[:500]}")
        return True
    except Exception as e:
        print(f"❌ Audio Music FAILED: {e}")
        return False


async def main():
    print("=" * 60)
    print("fal.ai API Key Test Suite")
    print("=" * 60)

    results = {}
    results["image"] = await test_image()
    results["video"] = await test_video()
    results["audio_tts"] = await test_audio_tts()
    results["audio_music"] = await test_audio_music()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {test_name}: {status}")

    all_passed = all(results.values())
    print(f"\nOverall: {'✅ ALL PASSED' if all_passed else '❌ SOME FAILED'}")
    return all_passed


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
