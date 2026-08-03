import { cva, type VariantProps } from 'class-variance-authority'
import { Slot } from 'radix-ui'
import * as React from 'react'

import { cn } from '@/lib/utils'

// Text+icon actions underline the label on hover, not the glyph.
const TEXT_ACTION_ICON = '[&_.codicon]:no-underline [&_svg]:no-underline'

// Slacc (DESIGN.md): text buttons are pills (90px); icon buttons stay compact.
const buttonVariants = cva(
  "inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 rounded-[90px] text-xs leading-4 font-bold whitespace-nowrap shadow-none transition-all duration-100 outline-none focus-visible:border-ring focus-visible:ring-[0.1875rem] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-default disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5",
  {
    variants: {
      variant: {
        // button-primary-pill — aubergine + white
        default: 'bg-primary text-primary-foreground hover:bg-[color-mix(in_srgb,#611f69_100%,transparent)]',
        destructive:
          'bg-destructive text-white hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40',
        // button-outline-aubergine
        outline:
          'bg-white text-primary shadow-[inset_0_0_0_2px_#4a154b] hover:bg-[#f9f0ff] hover:text-primary',
        // button-secondary-pill — lavender + ink
        secondary: 'bg-[#f9f0ff] text-[#1d1d1d] hover:bg-[color-mix(in_srgb,#4a154b_10%,#f9f0ff)] hover:text-[#1d1d1d]',
        ghost: 'text-(--ui-text-secondary) hover:bg-[#f9f0ff] hover:text-[#1d1d1d]',
        link: `text-[#1264a3] underline-offset-4 decoration-current/20 hover:text-[#3860be] hover:underline ${TEXT_ACTION_ICON}`,
        text: `text-[#696969] underline-offset-4 hover:text-[#1d1d1d] hover:underline ${TEXT_ACTION_ICON}`,
        textStrong: `font-semibold text-[#696969] underline underline-offset-4 hover:text-[#1d1d1d] ${TEXT_ACTION_ICON}`
      },
      size: {
        // Generous horizontal padding — Slacc over-padded pill
        default: 'px-7 py-3.5 text-[0.9375rem] leading-[1.38] has-[>svg]:px-5',
        xs: "gap-1 px-3 py-1 text-[0.6875rem] leading-4 has-[>svg]:px-2 [&_svg:not([class*='size-'])]:size-3",
        sm: 'px-5 py-2 has-[>svg]:px-3',
        lg: 'px-8 py-3.5 text-[1.125rem] leading-5 has-[>svg]:px-5',
        inline: 'h-auto gap-1 rounded-none p-0 has-[>svg]:px-0',
        micro:
          "h-auto gap-0.5 rounded-none px-1 py-0 text-xs leading-4 font-normal has-[>svg]:px-0.5 [&_svg:not([class*='size-'])]:size-3",
        icon: 'size-9 rounded-full',
        'icon-xs': "size-6 rounded-full [&_svg:not([class*='size-'])]:size-3",
        'icon-sm': 'size-8 rounded-full',
        'icon-lg': 'size-10 rounded-full',
        'icon-titlebar':
          'h-(--titlebar-control-height) w-(--titlebar-control-size) rounded-full [&_.codicon]:text-[0.875rem]'
      }
    },
    compoundVariants: [
      {
        variant: 'textStrong',
        class: 'px-0 has-[>svg]:px-0'
      }
    ],
    defaultVariants: {
      variant: 'default',
      size: 'default'
    }
  }
)

function Button({
  className,
  variant = 'default',
  size = 'default',
  asChild = false,
  ...props
}: React.ComponentProps<'button'> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }) {
  const Comp = asChild ? Slot.Root : 'button'

  return (
    <Comp
      className={cn(buttonVariants({ variant, size }), className)}
      data-size={size}
      data-slot="button"
      data-variant={variant}
      {...props}
    />
  )
}

export { Button, buttonVariants }
