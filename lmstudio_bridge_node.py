"""
@author: caradat
@title: LM Studio Bridge
@description: This extension provides a custom node for ComfyUI that integrates with LM Studio's REST API.
Uses the native LM Studio API format.
https://lmstudio.ai/docs/developer/rest/chat
"""

import base64
import hashlib
import io
import requests
import numpy as np
from PIL import Image
import urllib.parse

# Global session for keep-alive support
_http_session = requests.Session()

# Default server settings
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 1234

# API endpoints
LMSTUDIO_API_CHAT_ENDPOINT = "/api/v1/chat"
MODELS_ENDPOINT = "/api/v1/models"

class LMStudioBridge:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"default": "Make the ready-to-use prompt with the image description:"}),
                "system_prompt": ("STRING", {"default": "You are a helpful AI assistant."}),
                "host": ("STRING", {"default": DEFAULT_HOST}),
                "port": ("INT", {"default": DEFAULT_PORT, "min": 1, "max": 65535}),
                "max_output_tokens": ("INT", {"default": 2000, "min": 1, "max": 4096}),
                "temperature": ("FLOAT", {"default": 0.6, "min": 0.0, "max": 2.0, "step": 0.1}),
            },
            "optional": {
                "image": ("IMAGE",),
                "model_key": ("STRING", {"default": ""}),
                "debug": ("BOOLEAN", {"default": False}),
                "timeout_seconds": ("INT", {"default": 300, "min": 10, "max": 3600, "step": 1}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("Generated Text",)
    FUNCTION = "generate_text"
    CATEGORY = "ComfyExpo/REST"

    @classmethod
    def IS_CHANGED(cls, prompt, system_prompt, host, port, max_output_tokens, temperature,
                   image=None, model_key="",
                   debug=False, timeout_seconds=300):
        h = hashlib.sha256()
        h.update(str(prompt).encode())
        h.update(str(system_prompt).encode())
        h.update(str(host).encode())
        h.update(str(port).encode())
        h.update(str(max_output_tokens).encode())
        h.update(str(temperature).encode())
        h.update(str(model_key).encode())
        h.update(str(debug).encode())
        h.update(str(timeout_seconds).encode())

        if image is not None:
            try:
                if hasattr(image, 'cpu'):
                    img_np = image.cpu().numpy()
                else:
                    img_np = np.array(image)
                if img_np.dtype != np.uint8:
                    img_np = (np.clip(img_np, 0, 1) * 255).astype(np.uint8)

                if img_np.ndim == 4:
                    img_np = img_np[0]
                h.update(img_np.tobytes())
            except Exception:
                h.update(b"image_error_placeholder")
        return int(h.hexdigest(), 16)

    MAX_IMAGE_DIMENSION = 1536  # Adjust based on your model's capabilities

    def _resize_image_if_needed(self, pil_image):
        """Resize image if it exceeds max dimension while maintaining aspect ratio."""
        width, height = pil_image.size
        if max(width, height) > self.MAX_IMAGE_DIMENSION:
            ratio = self.MAX_IMAGE_DIMENSION / max(width, height)
            new_size = (int(width * ratio), int(height * ratio))

            # Pillow version compatibility for resampling filter
            try:
                # Pillow 10.0.0+
                resample_filter = Image.Resampling.LANCZOS
            except AttributeError:
                # Pillow < 10.0.0
                resample_filter = Image.LANCZOS

            return pil_image.resize(new_size, resample_filter)
        return pil_image

    def _encode_image(self, image_tensor):
        """
        Convert ComfyUI image tensor to base64 PNG with optimizations.
        Returns (base64_string, mime_type).
        """
        # 1. Efficient Tensor to Numpy conversion
        # Ensure contiguous memory layout for PIL
        img_array = np.clip(image_tensor, 0, 1)
        img_array = (img_array * 255).astype(np.uint8)

        # 2. Create PIL Image
        if img_array.shape[-1] == 4:
            mode = 'RGBA'
        else:
            mode = 'RGB'

        pil_image = Image.fromarray(img_array, mode=mode)

        # 3. Optimization: Resize if too large (Reduces token/bandwidth usage)
        pil_image = self._resize_image_if_needed(pil_image)

        # 4. Optimization: Check Alpha Channel (Save RGB if alpha is unused)
        if pil_image.mode == 'RGBA':
            # Check if alpha channel is fully opaque
            alpha = pil_image.split()[3]
            if alpha.getextrema()[0] == 255:  # Min value is 255, so all are opaque
                pil_image = pil_image.convert('RGB')
                mode = 'RGB'

        # 5. Encode to PNG with compression optimization
        buffer = io.BytesIO()
        save_kwargs = {
            'format': 'PNG',
            'optimize': True,        # Enables optimization passes
            'compress_level': 6      # Balance between speed and size (0-9)
        }

        # PNG does not support 'quality' parameter like JPEG, but optimize helps significantly
        pil_image.save(buffer, **save_kwargs)

        img_b64 = base64.b64encode(buffer.getvalue()).decode()
        mime = 'image/png'

        return img_b64, mime

    def _determine_model(self, host, port, model_key, debug):
        """
        Determines the model ID to use.
        If model_key is provided, returns it.
        Otherwise queries the server for the list of loaded models and returns the first one.
        Returns a tuple (model_id, error_message). error_message is None on success.
        """

        # If the model is explicitly specified, do not use the cache
        if model_key:
            return model_key, None

        # Автоматическое определение модели (запрос всегда выполняется при пустом model_key)
        base_url = f"http://{host}:{port}"
        url = urllib.parse.urljoin(base_url, MODELS_ENDPOINT)

        try:
            if debug:
                print(f"[LM Studio Bridge] Auto-detecting loaded model from {url}")
            resp = _http_session.get(url, timeout=(5, 5))
            resp.raise_for_status()
            data = resp.json()
            models = data.get("models", [])

            if not models:
                return None, ("Error: The LM Studio server is accessible, but no models are known. "
                              "Load a model in LM Studio and try again.")

            for model in models:
                if isinstance(model, dict) and model.get("loaded_instances"):
                    model_id = model.get("key")
                    if debug:
                        print(f"[LM Studio Bridge] Найдена загруженная модель: {model_id}")
                    return model_id, None

        except requests.exceptions.ConnectionError:
            return None, (f"Error: Cannot connect to the LM Studio server at {host}:{port}. "
                          "Ensure LM Studio is running and the server is enabled.")
        except requests.exceptions.Timeout:
            return None, "Error: The LM Studio server did not respond to the model list request within 5 seconds."
        except requests.exceptions.RequestException as e:
            return None, f"Error when requesting the model list: {str(e)}"

    def generate_text(self, prompt, system_prompt, host, port, max_output_tokens, temperature,
                      image=None, model_key="",
                      debug=False, timeout_seconds=300):
        debug = bool(debug)

        # Determine which model to use
        effective_model, error = self._determine_model(host, port, model_key, debug)
        if error:
            print(f"[LM Studio Bridge] {error}")
            return (error,)

        # If the model was auto-detected (model_key was empty), update the model_key output to show the used model
        if not model_key and effective_model:
            model_key = effective_model

        if debug:
            print(f"[LM Studio Bridge] Starting generation")
            print(f"[LM Studio Bridge] Model: {effective_model}")
            print(f"[LM Studio Bridge] Server: {host}:{port}")
            print(f"[LM Studio Bridge] Max tokens: {max_output_tokens}, Temperature: {temperature}")
            print(f"[LM Studio Bridge] Image provided: {image is not None}")

        # Encode image if present
        img_b64 = None
        img_mime = None
        if image is not None:
            try:
                img_np = image.cpu().numpy() if hasattr(image, 'cpu') else np.array(image)
                if img_np.ndim == 4:
                    img_np = img_np[0]

                img_b64, img_mime = self._encode_image(img_np)
                if debug:
                    print(f"[LM Studio Bridge] Image encoded as PNG, base64 length: {len(img_b64)}")
            except Exception as e:
                error_msg = f"Image encoding failed: {str(e)}"
                print(f"[LM Studio Bridge] Error: {error_msg}")
                return (error_msg,)

        # Prepare payload for LM Studio API
        payload = {
            "model": effective_model,
            "input": prompt if not img_b64 else [
                {"type": "text", "content": prompt},
                {"type": "image", "data_url": f"data:{img_mime};base64,{img_b64}"}
            ],
            "system_prompt": system_prompt,
            "max_output_tokens": max_output_tokens,
            "temperature": temperature
        }

        # Добавляем модель только если явно указана
        if model_key:
            payload["model"] = model_key

        if img_b64:
            data_url = f"data:{img_mime};base64,{img_b64}"
            # Use input object with type "image" for image input
            payload["input"] = [
                {"type": "text", "content": prompt},
                {"type": "image", "data_url": data_url}
            ]

        base_url = f"http://{host}:{port}"
        url = urllib.parse.urljoin(base_url, LMSTUDIO_API_CHAT_ENDPOINT)

        headers = {"Content-Type": "application/json"}

        if debug:
            # Truncate image data for logging
            debug_payload = payload.copy()
            # Mask sensitive data in debug logs
            if 'input' in debug_payload:
                if isinstance(debug_payload['input'], list):
                    for item in debug_payload['input']:
                        if isinstance(item, dict) and item.get('type') == 'text':
                            content = item.get('content', '')
                            if len(content) > 100:
                                item['content'] = content[:50] + '...[TRUNCATED]...' + content[-50:]
            else:
                # This is a string, can be truncated if desired
                if isinstance(debug_payload['input'], str) and len(debug_payload['input']) > 100:
                    debug_payload['input'] = debug_payload['input'][:50] + '...[TRUNCATED]...' + debug_payload['input'][-50:]

            print(f"[LM Studio Bridge] Payload: {debug_payload}")
        try:
            if debug:
                print(f"[LM Studio Bridge] Sending request to {url}")

            response = _http_session.post(url, json=payload, headers=headers, timeout=(timeout_seconds, timeout_seconds))

            if debug:
                print(f"[LM Studio Bridge] Response status: {response.status_code}")

            response.raise_for_status()
            data = response.json()

            # Extract generated text
            generated_text = ""
            if "output" in data:
                for item in data["output"]:
                    if item.get("type") == "message":
                        generated_text += item.get("content", "")
            else:
                generated_text = "Unexpected response format from server."

            if not generated_text:
                generated_text = "No content generated."

            # Удаление лишних пробелов и абзацев в начале и в конце
            generated_text = generated_text.strip()

            if debug:
                preview = generated_text[:200] + "..." if len(generated_text) > 200 else generated_text
                print(f"[LM Studio Bridge] Generated text: {preview}")

            return (generated_text,)

        except requests.exceptions.ConnectionError:
            error_msg = f"Error: Cannot connect to LM Studio server at {host}:{port}. Make sure LM Studio is running and the server is enabled."
            print(f"[LM Studio Bridge] {error_msg}")
            return (error_msg,)
        except requests.exceptions.Timeout:
            error_msg = f"Error: LM Studio server response timed out after {timeout_seconds} seconds."
            print(f"[LM Studio Bridge] {error_msg}")
            return (error_msg,)
        except requests.exceptions.RequestException as e:
            error_msg = f"Error: Request to LM Studio server failed: {str(e)}"
            print(f"[LM Studio Bridge] {error_msg}")
            return (error_msg,)
        except Exception as e:
            error_msg = f"LM Studio Bridge Error: {str(e)}"
            print(f"[LM Studio Bridge] {error_msg}")
            return (error_msg,)

# Node registration is handled in __init__.py