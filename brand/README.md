# Brand assets for home-assistant/brands

Properly-sized assets ready to drop into the
[home-assistant/brands](https://github.com/home-assistant/brands) repository.

## How to submit

1. Fork https://github.com/home-assistant/brands
2. Create folder `custom_integrations/espeasy_p2p/`
3. Copy the four files from this directory into it:
   - `icon.png`      (256×256)
   - `icon@2x.png`   (512×512)
   - `logo.png`      (max height 128)
   - `logo@2x.png`   (max height 256)
4. Open a PR titled `Add espeasy_p2p`
5. The brands bot validates automatically; merges typically within a few days.

After the brands PR is merged, open the HACS default-repository PR at
https://github.com/hacs/default and add the line
`chance-konstruktion/ha-espeasy-p2p` to the `integration` file.
