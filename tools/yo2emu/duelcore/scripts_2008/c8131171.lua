--キラー・スネーク
--Sinister Serpent (texto de 2008)
--Texto del cliente 2008 (merge.dat, card_desc_e): "During your Standby Phase, if a "Sinister Serpent" exists in your
--Graveyard, you can return the "Sinister Serpent" to your hand."
--Diferencias con CardScripts (errata moderna): (1) fuera 'also banish 1 "Sinister Serpent" from your GY during your
--opponent's next End Phase' (el destierro de la End Phase del rival); (2) fuera 'You can only use this effect of "Sinister
--Serpent" once per turn' (con varias Sinister Serpent en el Cementerio cada una vuelve con su efecto).
local s,id=GetID()
function s.initial_effect(c)
	--Add this card to the hand
	local e1=Effect.CreateEffect(c)
	e1:SetDescription(aux.Stringid(id,0))
	e1:SetCategory(CATEGORY_TOHAND)
	e1:SetType(EFFECT_TYPE_FIELD+EFFECT_TYPE_TRIGGER_O)
	e1:SetCode(EVENT_PHASE|PHASE_STANDBY)
	e1:SetRange(LOCATION_GRAVE)
	e1:SetCondition(function(_,tp) return Duel.IsTurnPlayer(tp) end)
	e1:SetTarget(s.target)
	e1:SetOperation(s.operation)
	c:RegisterEffect(e1)
end
s.listed_names={id}
function s.target(e,tp,eg,ep,ev,re,r,rp,chk)
	local c=e:GetHandler()
	if chk==0 then return c:IsAbleToHand() end
	Duel.SetOperationInfo(0,CATEGORY_TOHAND,c,1,tp,0)
end
function s.operation(e,tp,eg,ep,ev,re,r,rp)
	local c=e:GetHandler()
	if c:IsRelateToEffect(e) then
		Duel.SendtoHand(c,nil,REASON_EFFECT)
	end
end
