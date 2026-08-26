import { cva, type VariantProps } from 'class-variance-authority'
import { Slot } from 'radix-ui'
import * as React from 'react'

import { cn } from '@/lib/utils'

// Text+icon actions underline the label on hover, not the glyph.
const TEXT_ACTION_ICON = '[&_.codicon]:no-underline [&_svg]:no-underline'

// Slacc (DESIGN.md): text buttons are pills (90px); icon buttons stay compact.
const buttonVariants = cva(
  "inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 rounded-[9px] text-xs leading-4 font-medium whitespace-nowrap shadow-none transition-all duration-100 outline-none focus-visible:border-ring focus-visible:ring-[0.1875rem] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-default disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5",
  {
    variants: {
      variant: {
        // button-primary — mock purple CTA
        default: 'rounded-[9px] bg-primary text-primary-foreground shadow-[0_4px_14px_rgba(113,55,245,0.28)] hover:bg-[color-mix(in_srgb,#7137f5_88%,#1e2033)]',
        destructive:
          'rounded-[9px] bg-destructive text-white hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40',
        // button-outline — white + purple ring
        outline: 'rounded-[9px] bg-white text-[#1e2033] shadow-[inset_0_0_0_1px_#e8eaf0] hover:bg-[#f4f5f8] hover:text-[#7137f5]',
        // button-secondary — cool gray fill
        secondary: 'rounded-[9px] bg-[#f4f5f8] text-[#1e2033] hover:bg-[color-mix(in_srgb,#7137f5_10%,#f4f5f8)] hover:text-[#1e2033]',
        ghost: 'rounded-[9px] text-(--ui-text-secondary) hover:bg-[#f4f5f8] hover:text-[#1e2033]',
        link: `text-[#7137f5] underline-offset-4 decoration-current/20 hover:text-[#ff4165] hover:underline ${TEXT_ACTION_ICON}`,
        text: `text-[#858ba3] underline-offset-4 hover:text-[#1e2033] hover:underline ${TEXT_ACTION_ICON}`,
        textStrong: `font-medium text-[#858ba3] underline underline-offset-4 hover:text-[#1e2033] ${TEXT_ACTION_ICON}`
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
